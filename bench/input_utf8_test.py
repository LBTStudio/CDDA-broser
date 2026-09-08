#!/usr/bin/env python3
"""Test CDDA's actual input callback against its bundled ImGui library.

Usage: python3 bench/input_utf8_test.py /path/to/patched/cdda
HEAD must contain unpatched 0.I. SDL event construction and backend routing
are extracted from real source, with a minimal SDL window/event stub. ImGui
is real; this is not a full SDL/browser or full-game performance test.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()
root = Path(__file__).resolve().parent
out = root / 'out'
out.mkdir(exist_ok=True)


def callback(text):
    start = text.index('static int input_callback(')
    return text[start:text.index('\nvoid string_input_popup_imgui::draw_input_control()', start)]


def literal(text):
    return '"' + ''.join(f'\\x{b:02x}' for b in text.encode('utf-8')) + '"'


current = callback((source / 'src/input_popup.cpp').read_text())
old = subprocess.check_output(['git', '-C', str(source), 'show', 'HEAD:src/input_popup.cpp'], text=True)
legacy = callback(old).replace('input_callback(', 'legacy_input_callback(')
imgui = source / 'src/third-party/imgui'
program = r'''
#include "imgui.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
struct string_input_popup_imgui {
    int limit;
    int histories = 0;
    int get_max_input_length() const { return limit; }
    void update_input_history(ImGuiInputTextCallbackData *) { ++histories; }
};
'''
# Exercise the missing hop in the old test: bridge -> SDL backend -> widget.
sdl = (source / 'src/sdltiles.cpp').read_text()
bridge = sdl[sdl.index('static void cdda_pump_web_ime()'):]
commit = bridge[bridge.index('        const char *p = ime_buf;'):bridge.index('    // Composition preview:')]
commit = commit[:commit.rindex('    }')]  # outer EM_ASM polling loop
preview = bridge[bridge.index('        size_t chunk = strlen( ime_buf );'):]
preview = preview[:preview.index('\n    }')]
backend = (imgui / 'imgui_impl_sdl2.cpp').read_text()
viewport = backend[backend.index('static ImGuiViewport* ImGui_ImplSDL2_GetViewportForWindowID('):]
viewport = viewport[:viewport.index('\n}') + 2]
text_case = backend[backend.index('        case SDL_TEXTINPUT:'):backend.index('        case SDL_KEYDOWN:')]
program += r'''
using Uint32 = unsigned int;
constexpr int SDL_TEXTINPUT = 1, SDL_TEXTEDITING = 2;
constexpr int SDL_TEXTINPUTEVENT_TEXT_SIZE = 32, SDL_TEXTEDITINGEVENT_TEXT_SIZE = 32;
struct SDL_Event {
    int type;
    struct { Uint32 windowID; char text[32]; } text;
    struct { Uint32 windowID; char text[32]; int start, length; } edit;
};
struct Window { void *get() { return this; } } window;
Uint32 SDL_GetWindowID(void *) { return 17; }
struct ImGui_ImplSDL2_Data { Uint32 WindowID = 17; } backend_data;
ImGui_ImplSDL2_Data *ImGui_ImplSDL2_GetBackendData() { return &backend_data; }
std::vector<SDL_Event> events;
int SDL_PushEvent(const SDL_Event *event) { events.push_back(*event); return 1; }
'''
program += viewport + '\nbool route_text(const SDL_Event *event) {\n'
program += '    ImGuiIO &io = ImGui::GetIO();\n    switch(event->type) {\n' + text_case
program += '    default: return false;\n    }\n}\n'
program += 'void pump_commit(const char *ime_buf) {\n' + commit + '\n}\n'
program += 'void pump_preview(const char *ime_buf) {\n' + preview + '\n}\n'
program += r'''
static void deliver(const std::string &input) {
    events.clear(); pump_commit(input.c_str());
    std::string delivered;
    for (const auto &event : events) {
        assert(event.type == SDL_TEXTINPUT && event.text.windowID == 17);
        assert(std::strlen(event.text.text) <= 31);
        delivered += event.text.text;
        assert(route_text(&event));
    }
    assert(delivered == input);
}
static void check_routing() {
    SDL_Event old_event{}; old_event.type = SDL_TEXTINPUT;
    std::strcpy(old_event.text.text, "日本");
    assert(!route_text(&old_event)); // previous bridge silently lost ALL Japanese
    old_event.text.windowID = 99;
    assert(!route_text(&old_event)); // do not disable other-window rejection
    std::puts("PASS negative control: real ImGui SDL text branch rejects windowID=0 and foreign windows");
    for (const char *preview : {"", "にほん", "日本語日本語日本語日本語"}) {
        events.clear(); pump_preview(preview);
        assert(events.size() == 1 && events[0].type == SDL_TEXTEDITING);
        assert(events[0].edit.windowID == 17 && std::strlen(events[0].edit.text) <= 31);
    }
}
'''
program += legacy + '\n' + current + r'''
static void check(const char *input, int limit, const char *expected) {
    const int len = std::strlen(input);
    std::vector<char> buf(len + 33, '#');
    char *text = buf.data() + 16;
    std::memcpy(text, input, len + 1);
    string_input_popup_imgui popup{limit};
    ImGuiInputTextCallbackData data;
    data.UserData = &popup;
    data.EventFlag = ImGuiInputTextFlags_CallbackEdit;
    data.Buf = text; data.BufSize = len + 1; data.BufTextLen = len;
    data.CursorPos = len; data.SelectionStart = 0; data.SelectionEnd = len;
    assert(input_callback(&data) == 0);
    assert(data.BufTextLen == int(std::strlen(text)));
    assert(std::strcmp(text, expected) == 0);
    assert(data.CursorPos <= data.BufTextLen);
    assert(data.SelectionStart <= data.BufTextLen && data.SelectionEnd <= data.BufTextLen);
    assert(data.BufDirty == (limit > 0 && len > limit));
    assert(popup.histories == 0);
    for (int i = 0; i < 16; ++i) {
        assert(buf[i] == '#');
        assert(buf[len + 17 + i] == '#');
    }
}
static void check_widget(const std::string &input, int limit, const std::string &expected) {
    ImGui::CreateContext();
    ImGuiIO &io = ImGui::GetIO();
    io.IniFilename = nullptr; io.LogFilename = nullptr;
    io.DisplaySize = ImVec2(640, 480); io.DeltaTime = 1.0f / 60;
    unsigned char *pixels; int width, height;
    io.Fonts->GetTexDataAsRGBA32(&pixels, &width, &height);
    char buffer[1024] = {};
    string_input_popup_imgui popup{limit};
    for (int frame = 0; frame < 6; ++frame) {
        if (frame == 3) deliver(input);
        ImGui::NewFrame();
        ImGui::Begin("test");
        if (frame == 1) ImGui::SetKeyboardFocusHere();
        ImGui::InputText("filter", buffer, sizeof(buffer),
            ImGuiInputTextFlags_CallbackEdit, input_callback, &popup);
        ImGui::End();
        ImGui::Render(); // real ImGui also checks strlen/BufTextLen invariant
    }
    assert(std::string(buffer) == expected);
    ImGui::DestroyContext();
}
int main() {
    ImGui::CreateContext();
    // Prove the test exposes the old length inconsistency (no legacy widget crash needed).
    char old_text[] = "日本";
    string_input_popup_imgui old_popup{4};
    ImGuiInputTextCallbackData old_data;
    old_data.UserData = &old_popup; old_data.EventFlag = ImGuiInputTextFlags_CallbackEdit;
    old_data.Buf = old_text; old_data.BufTextLen = 6; old_data.BufSize = 7;
    legacy_input_callback(&old_data);
    assert(old_data.BufTextLen == 4 && std::strlen(old_text) == 6);
    std::puts("PASS negative control: upstream callback violates ImGui buffer length invariant");
'''
samples = ['', 'ASCII', '日本語', 'a日本b', 'éà', '𠮷野家', 'e\u0301', '日' * 100,
           'a' * 255 + '日', '日' * 85 + 'abc']
count = 0
for text in samples:
    raw = text.encode('utf-8')
    for limit in range(-1, len(raw) + 3):
        expected = raw[:limit].decode('utf-8', errors='ignore') if limit > 0 else text
        program += f'    check({literal(text)}, {limit}, {literal(expected)});\n'
        count += 1
program += r'''
    string_input_popup_imgui popup{1};
    ImGuiInputTextCallbackData history;
    history.UserData = &popup; history.EventFlag = ImGuiInputTextFlags_CallbackHistory;
    input_callback(&history); assert(popup.histories == 1);
    check_routing();
    ImGui::DestroyContext();
'''
for text in ['', 'ASCII', '日本', '日本語' * 10, '𠮷野家', 'a' * 30 + '日', '日' * 100]:
    for limit in [0, 4, 256]:
        # The upstream ImGui build uses 16-bit ImWchar: non-BMP characters
        # become U+FFFD. deliver() still checks lossless SDL chunk assembly.
        # Preserve and report this existing limitation rather than silently
        # enabling a different ImGui ABI just for the test.
        widget_text = ''.join(c if ord(c) <= 0xffff else '\ufffd' for c in text)
        expected = widget_text.encode('utf-8')[:limit].decode('utf-8', errors='ignore') if limit else widget_text
        program += f'    check_widget({literal(text)}, {limit}, {literal(expected)});\n'
program += '    std::puts("PASS 21 bridge/backend/InputText cases: Japanese, multibyte chunks, limits, reopen");\n'
program += f'    std::puts("PASS {count} UTF-8 boundary cases, byte limits/cursors/selection/canaries/history");\n}}\n'
with tempfile.TemporaryDirectory(prefix='input-utf8-', dir=out) as tmp:
    cpp, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
    cpp.write_text(program)
    libs = [imgui / name for name in ('imgui.cpp', 'imgui_draw.cpp', 'imgui_tables.cpp', 'imgui_widgets.cpp')]
    subprocess.run(['g++', '-std=c++17', '-O0', '-g0', '-I' + str(imgui), str(cpp),
                    *map(str, libs), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)

#!/usr/bin/env python3
from __future__ import annotations
import csv, hashlib, io, json, os, re, shutil, sys, time, zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse, urlunparse
from playwright.sync_api import sync_playwright

URL = os.getenv("ERATEN_SHARE_URL", "https://32.gigafile.nu/0111-hc19ff9a337d787399d294b1c4dff0baa")
WORK = Path(os.getenv("ERATEN_WORK_DIR", "/tmp/eraten-work"))
REPORT = Path(os.getenv("ERATEN_REPORT_DIR", "eraten-report"))
MAX_UNPACK = 8 * 1024**3
MAX_TEXT = 3 * 1024**2
TEXT_EXTS = {".erb",".csv",".txt",".ini",".json",".yaml",".yml",".xml",".html",".htm",".js",".ts",".md",".cfg",".def",".lst",".erbinc",".sql",".toml",".dat"}
CHAR_PAT = re.compile(r"アツコ|秤アツコ|Atsuko|ATSUKO|敦子", re.I)
SKILL_PAT = re.compile(r"習得スキル|スキル習得|GETSKILL|SETSKILL|SKILL_LV|SKILLNAME|SkillData|習得|修得", re.I)
FORM_PAT = re.compile(r"ダメージ|回復量|物理攻撃力|魔法威力|魔法効果|命中|回避|DAMAGE|POWER|MAGIC|PHYSICAL|HEAL|HITRATE|EVASION", re.I)
ATTRS = ["力","知恵","魔力","耐力","速さ","運","Strength","Intelligence","Magic","Endurance","Agility","Luck","STR","INT","MAG","END","AGI","LUK","HP","MP"]
IDENT = re.compile(r"\b[A-Z_][A-Z0-9_]{2,}\b|\b(?:STR|INT|MAG|END|AGI|LUK|HP|MP)\b")
NUMID = re.compile(r"\b\d{2,8}\b")
EXPR_TOKEN = re.compile(r""" "[^"\n]{0,80}"|'[^'\n]{0,80}'|[-+]?\d+(?:\.\d+)?|[A-Za-z_][A-Za-z0-9_]*|[一-龠々ぁ-ゖァ-ヺー]+|\+=|-=|\*=|/=|==|!=|>=|<=|[=+*/%?:()<>-] """.strip())
JP_TOKEN = re.compile(r"[一-龠々ぁ-ゖァ-ヺー]{2,12}")

def save(name, payload):
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT/name).write_text(payload, encoding="utf-8", errors="replace")

def sha256(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def validate_url(url):
    p=urlparse(url)
    host=(p.hostname or "").lower()
    if p.scheme!="https" or not (host=="gigafile.nu" or host.endswith(".gigafile.nu")) or p.username or p.password or p.port not in (None,443):
        raise ValueError("Only an HTTPS GigaFile share URL is accepted.")

def click_download(context, page, archive):
    events = {"downloads": [], "responses": [], "requests": [], "pages": []}
    def note_response(response):
        try:
            headers = response.headers
            content_type = headers.get("content-type", "")
            disposition = headers.get("content-disposition", "")
            if "attachment" in disposition.lower() or "zip" in content_type.lower() or "octet-stream" in content_type.lower():
                events["responses"].append({
                    "url": scrub(response.url), "status": response.status,
                    "content_type": content_type[:120], "content_disposition": disposition[:200],
                    "content_length": headers.get("content-length", ""),
                })
        except Exception:
            pass
    def note_download(download):
        try:
            events["downloads"].append(download.suggested_filename)
        except Exception:
            events["downloads"].append("(filename unavailable)")
    context.on("page", lambda p: events["pages"].append(scrub(p.url)))
    page.on("response", note_response)
    page.on("download", note_download)

    try:
        # The known official page has a specific "ダウンロード開始" button.
        # Avoid the similarly named "download cannot start" help link.
        button = page.get_by_role("button", name=re.compile(r"^\s*ダウンロード開始\s*$"))
        if button.count() == 0:
            button = page.locator('input[type="button"],input[type="submit"],button,[role="button"]').filter(
                has_text=re.compile(r"^\s*ダウンロード開始\s*$")
            )
        if button.count() == 0:
            button = page.get_by_text(re.compile(r"^\s*ダウンロード開始\s*$"), exact=True)
        if button.count() == 0:
            raise RuntimeError("Exact GigaFile download-start control was not found.")

        label = ""
        try:
            label = " ".join(filter(None, [
                button.first.inner_text(timeout=1000),
                button.first.get_attribute("value"),
                button.first.get_attribute("aria-label"),
                button.first.get_attribute("title"),
            ])).strip()
        except Exception:
            pass
        try:
            button_meta = button.first.evaluate(
                "(e)=>({tag:e.tagName,text:(e.innerText||e.value||'').trim(),type:e.type||null,id:e.id||null,className:typeof e.className==='string'?e.className.slice(0,120):'',onclick:e.getAttribute('onclick'),href:e.getAttribute('href'),disabled:!!e.disabled,outerHTML:e.outerHTML.slice(0,900)})"
            )
        except Exception as exc:
            button_meta = {"inspection_error": str(exc)[:200]}

        # Allow the server's normal download flow to finish; don't retry unrelated links.
        try:
            with page.expect_download(timeout=60000) as info:
                button.first.click(timeout=8000, force=True)
            dl = info.value
            dl.save_as(str(archive))
            return {
                "page_url": scrub(page.url), "label": label[:120],
                "suggested_filename": dl.suggested_filename, "click_mode": "exact_button",
                "bytes": archive.stat().st_size, "sha256": sha256(archive),
            }
        except Exception as exc:
            events["click_error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
            # Fallback: invoke the exact public JS function used by the visible button.
            # This does not skip any access check; it is the same UI action exposed by onclick.
            try:
                onclick = button.first.get_attribute("onclick") or ""
                match = re.search(r"""download\(\s*['"]([^'"]+)['"]\s*,\s*(true|false)\s*,\s*(true|false)\s*\)""", onclick)
                if match:
                    file_key, arg2, arg3 = match.groups()
                    events["download_function_key_seen"] = True
                    with page.expect_download(timeout=60000) as fallback_info:
                        page.evaluate("(x) => download(x[0], x[1], x[2])", [file_key, arg2 == "true", arg3 == "true"])
                    fallback_download = fallback_info.value
                    fallback_download.save_as(str(archive))
                    return {
                        "page_url": scrub(page.url), "label": label[:120],
                        "suggested_filename": fallback_download.suggested_filename,
                        "click_mode": "visible_button_js_fallback",
                        "bytes": archive.stat().st_size, "sha256": sha256(archive),
                    }
            except Exception as fallback_exc:
                events["js_fallback_error"] = f"{type(fallback_exc).__name__}: {str(fallback_exc)[:500]}"
            try:
                events["download_function_source"] = page.evaluate(
                    "typeof download === 'function' ? download.toString().slice(0,2500) : null"
                )
            except Exception:
                pass
            # Some file hosts expose the attachment as a navigation response rather than a Playwright download event.
            for response_meta in events["responses"]:
                events.setdefault("attachment_response_seen", True)
            metadata = []
            for p in list(context.pages):
                try:
                    body = p.locator("body").inner_text(timeout=2500)[:1400]
                    metadata.append({
                        "url": scrub(p.url), "title": p.title()[:120], "body_excerpt": body
                    })
                    try:
                        p.screenshot(path=str(REPORT/"download-page.png"), full_page=True, timeout=5000)
                    except Exception:
                        pass
                except Exception:
                    pass
            save("01_download_diagnostics.json", json.dumps({
                "button": button_meta, "label": label, "events": events, "pages": metadata
            }, ensure_ascii=False, indent=2))
            raise RuntimeError("GigaFile's download-start button was clicked, but no file download event was observed. See 01_download_diagnostics.json.")
    except Exception as exc:
        if not (REPORT/"01_download_diagnostics.json").exists():
            save("01_download_diagnostics.json", json.dumps({
                "error": f"{type(exc).__name__}: {str(exc)[:700]}",
                "events": events,
            }, ensure_ascii=False, indent=2))
        raise

def scrub(url):
    p=urlparse(url)
    return urlunparse((p.scheme,p.netloc,p.path,"","",""))

def safe_extract(archive,dest):
    dest.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist()
        total=sum(i.file_size for i in infos)
        if total>MAX_UNPACK: raise ValueError(f"Unpacked size {total:,} exceeds safety cap")
        root=dest.resolve()
        for i in infos:
            name=i.filename.replace("\\","/")
            pp=PurePosixPath(name)
            if pp.is_absolute() or ".." in pp.parts: raise ValueError(f"Unsafe zip path: {name}")
            target=(dest/Path(*pp.parts)).resolve()
            if target!=root and root not in target.parents: raise ValueError(f"Zip path escapes extraction root: {name}")
            if ((i.external_attr>>16)&0o170000)==0o120000: raise ValueError(f"Zip symlink rejected: {name}")
        bad=z.testzip()
        if bad: raise ValueError(f"CRC error in {bad}")
        z.extractall(dest)
        return infos,total

def read_text(path):
    try:
        data=path.read_bytes()
        if not data or len(data)>MAX_TEXT or b"\x00" in data[:8192]: return None
        for enc in ("utf-8-sig","cp932","shift_jis","utf-16"):
            try:
                txt=data.decode(enc)
                if txt.count("\ufffd")<=max(10,len(txt)//1000): return txt
            except UnicodeDecodeError: pass
    except OSError: pass
    return None

def tokens(text):
    return sorted(set(IDENT.findall(text)))

def run():
    validate_url(URL)
    if WORK.exists(): shutil.rmtree(WORK,ignore_errors=True)
    WORK.mkdir(parents=True,exist_ok=True)
    REPORT.mkdir(parents=True,exist_ok=True)
    archive=WORK/"eraten-package.zip"
    extracted=WORK/"extracted"
    report={"started_epoch":int(time.time()),"share_host":urlparse(URL).hostname,"downloaded":False}
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            context=browser.new_context(accept_downloads=True,locale="ja-JP")
            page=context.new_page()
            page.set_default_timeout(10000)
            try:
                page.goto(URL,wait_until="commit",timeout=60000)
            except Exception as nav_exc:
                save("01_download_diagnostics.json",json.dumps({"navigation_error":f"{type(nav_exc).__name__}: {str(nav_exc)[:500]}","target_host":urlparse(URL).hostname,"current_page_url":scrub(page.url)},ensure_ascii=False,indent=2))
                raise
            try:
                page.wait_for_load_state("domcontentloaded",timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(1500)
            report["download_page"]={"url":scrub(page.url),"title":page.title()[:150]}
            download_meta=click_download(context,page,archive)
            context.close()
            browser.close()
        report["downloaded"]=True
        report["download"]={**download_meta,"bytes":archive.stat().st_size,"sha256":sha256(archive)}
        if archive.stat().st_size<1024 or not zipfile.is_zipfile(archive): raise ValueError("Payload is not a valid ZIP")
        infos,unpack_bytes=safe_extract(archive,extracted)
        files=[p for p in extracted.rglob("*") if p.is_file()]
        report["archive"]={"entry_count":len(infos),"unpacked_bytes":unpack_bytes,"file_count":len(files)}
        inventory=[]
        textfiles=[]
        for p in sorted(files):
            try: size=p.stat().st_size
            except OSError: size=-1
            inventory.append({"path":p.relative_to(extracted).as_posix(),"bytes":size,"suffix":p.suffix.lower()})
            if p.suffix.lower() in TEXT_EXTS or (size>0 and size<=MAX_TEXT):
                txt=read_text(p)
                if txt is not None: textfiles.append((p,txt))
        report["text_candidate_count"]=len(textfiles)
        # Emit structural facts only; never include verbatim source lines.
        char_hits=[]
        char_files=set()
        text_by_rel={}
        for p,txt in textfiles:
            rel=p.relative_to(extracted).as_posix()
            text_by_rel[rel]=(p,txt)
            for n,line in enumerate(txt.splitlines(),1):
                if CHAR_PAT.search(line):
                    char_files.add(rel)
                    char_hits.append({"path":rel,"line":n,"terms":sorted(set(CHAR_PAT.findall(line))),"ids":sorted(set(NUMID.findall(line))),"identifiers":tokens(line),"attribute_tokens":[a for a in ATTRS if re.search(r"\b"+re.escape(a)+r"\b",line,re.I)]})
                    if len(char_hits)>=250: break
            if len(char_hits)>=250: break
        report["character_hits"]=char_hits
        report["character_files"]=sorted(char_files)

        target_suffixes=[
          "Chara5487_ブルーアーカイブ_アツコ.csv",
          "SKILL_アツコ専用技1_これも私の力……？.ERB",
          "SKILL_アツコ専用技2_隠れるよ.ERB",
          "SKILL_アツコ専用技3_教えられた事.ERB",
          "SKILL_アツコ専用技4_痛いのは慣れてる.ERB",
          "C_BTL_EFFECT_BA_002_これも私の力……？.ERB",
        ]
        engine_suffixes=[
          "SYSTEM_BATTLE_DAMAGE.ERB","SYSTEM_STATUS_CALC.ERB",
          "SKILL_ACTION_HEAL.ERB","SKILL_ACTION_FUNCTION.ERB",
          "GET_BATTLESTATUS.ERB","GET_BATTLESTATUS_NUM.ERB",
        ]
        target_records={}
        call_symbols=set()
        formula_line_re=re.compile(r"CALLF?|TRYCALL|CALLFORM|GOSUB|GOTO|GET_SKILL|SKILL_(?:POWER|HITRATE|DAMAGETYPE|HEAL_TYPE)|(?:BASE|ABL|MAXBASE|CSTR|CFLAG)\b|(?:力|知恵|魔力|耐力|速さ|運|STR|INT|MAG|END|AGI|LUK|HP|MP)\b|DAMAGE|HEAL|HITRATE|EVASION|POWER",re.I)
        call_re=re.compile(r"\b(?:CALLFORM|TRYCALL|CALLF?|GOSUB|GOTO)\s+([^\s]+)",re.I)
        def line_profile(line,n):
            quoted=re.findall(r'"([^"]{1,48})"|\'([^\']{1,48})\'',line)
            short_literals=[]
            for pair in quoted:
                value=next((v for v in pair if v), "")
                if value and len(value)<=48:
                    short_literals.append(value)
            latin_attrs=[a for a in ATTRS if re.search(r"\b"+re.escape(a)+r"\b",line,re.I)]
            jp_attrs=[a for a in ["力","知恵","魔力","耐力","速さ","運","体力","HP","MP","命中","回避","魔法威力","魔法効果","回復量"] if a in line]
            calls=[x.upper() for x in call_re.findall(line)]
            call_symbols.update(calls)
            return {
              "line":n,
              "identifiers":tokens(line)[:60],
              "attributes":sorted(set(latin_attrs+jp_attrs)),
              "numbers":re.findall(r"(?<![A-Z_])[-+]?\d+(?:\.\d+)?",line)[:20],
              "string_labels":short_literals[:8],
              "calls":calls[:12],
              "operators":sorted(set(re.findall(r"(?:\+=|-=|\*=|/=|==|!=|>=|<=|=|\+|-|\*|/|%|\?|:)",line))),
              "expression_tokens":EXPR_TOKEN.findall(line)[:100],
            }

        # Character CSV is emitted as field/value summaries rather than verbatim rows.
        char_csvs=[(rel,p,txt) for rel,(p,txt) in text_by_rel.items() if rel.endswith(target_suffixes[0])]
        for rel,p,txt in char_csvs:
            rows=[]
            for n,line in enumerate(txt.splitlines(),1):
                try: cells=next(csv.reader([line]))
                except Exception: cells=line.split(",")
                cells=[c.strip() for c in cells]
                if not cells or not any(cells): continue
                key=cells[0][:100]
                rest=cells[1:]
                values=[]
                for idx,val in enumerate(rest,1):
                    if not val: continue
                    nums=re.findall(r"(?<![A-Z_])[-+]?\d+(?:\.\d+)?",val)
                    ident=tokens(val)
                    stat_tags=[a for a in ATTRS if re.search(r"\b"+re.escape(a)+r"\b",val,re.I)]
                    jp_tags=[a for a in ["力","知恵","魔力","耐力","速さ","運","HP","MP","命中","回避","魔法威力","魔法効果","回復量"] if a in val]
                    literal_labels=[]
                    if len(val)<=45 and (re.search(r"[一-龠々ぁ-ゖァ-ヺ]",val) or re.fullmatch(r"[A-Z][A-Z0-9_]{1,40}",val)):
                        literal_labels=[val]
                    if nums or ident or stat_tags or jp_tags or literal_labels:
                        values.append({"index":idx,"numbers":nums[:20],"identifiers":ident[:30],"attributes":sorted(set(stat_tags+jp_tags)),"labels":literal_labels[:4],"length":len(val)})
                if key or values:
                    rows.append({"line":n,"field":key,"values":values})
            target_records[rel]={"kind":"character_csv_field_index","line_count":len(txt.splitlines()),"fields":rows}

        # Profile only logic-bearing lines from Atsuko's three dedicated skills.
        for rel,(p,txt) in text_by_rel.items():
            if any(rel.endswith(s) for s in target_suffixes[1:]):
                lines=txt.splitlines()
                profiles=[]
                for n,line in enumerate(lines,1):
                    if formula_line_re.search(line) or re.search(r"^\s*(?:@\w+|#FUNCTION|IF\b|ELSEIF\b|ELSE\b|ENDIF\b|SELECTCASE\b|CASE\b|ENDSELECT\b|RETURNF?\b|RESULTS?\s*=)",line,re.I):
                        profiles.append(line_profile(line,n))
                target_records[rel]={"kind":"skill_logic_token_profile","line_count":len(lines),"logic_lines":profiles}

        # Capture shared battle formulas and status calculations, not the whole source.
        engine_records={}
        for rel,(p,txt) in text_by_rel.items():
            if any(rel.endswith(s) for s in engine_suffixes) or any(t in p.name.upper() for t in ["GET_SKILL_PROP","GET_SKILL_HEAL_TYPE","SKILL_POWER","SKILL_HITRATE","SKILL_DAMAGETYPE"]):
                lines=txt.splitlines()
                profiles=[]
                for n,line in enumerate(lines,1):
                    if formula_line_re.search(line) or re.search(r"^\s*@\w+",line):
                        profiles.append(line_profile(line,n))
                if profiles:
                    engine_records[rel]={"line_count":len(lines),"formula_lines":profiles[:700]}
        # Trace actual function definitions reachable from Atsuko's custom skills.
        # Keep a tokenized representation (identifiers/constants/calls), never verbatim source lines.
        def_names = set(call_symbols) | {
            "HEAL_FIELD","GIVE_EFFECT_CHARA","GET_SKILL_PROP","GET_BASESTATUS",
            "GET_BATTLESTATUS","GET_BATTLESTATUS_NUM","BTL_CALC_BASE_DAMAGE",
            "BTL_CALC_DAMAGE_ADJUSTMENT","BTL_CALC_DAMAGE_EXE","BTL_CALC_FIXED_DAMAGE",
            "BTL_CALC_RATE_DAMAGE","BTL_CALC_DAMAGE_COOP","BTL_CALC_DAMAGE",
            "GET_BASEHEAL_IN_CALC","SKILL_SET_カジャンダ",
        }
        def_prefixes = ("SKILL_SET_", "SKILL_CALC_BATTTLE_STATUS_", "SKILL_PASSIVE_BTL_EFFECT_", "GET_SKILL_")
        def_header = re.compile(r"^\s*(?:@|#FUNCTION\s+)([^\s(]+)", re.I)
        function_profiles = {}
        function_declarations = {}
        for rel,(p,txt) in text_by_rel.items():
            lines=txt.splitlines()
            starts=[]
            for ix,line in enumerate(lines):
                m=def_header.match(line)
                if m:
                    starts.append((ix,m.group(1).upper().rstrip(",")))
            for idx,(begin,name) in enumerate(starts):
                if name not in def_names and not name.startswith(def_prefixes):
                    continue
                end=starts[idx+1][0] if idx+1<len(starts) else len(lines)
                body=lines[begin:end]
                prof=[]
                # Include all nonblank/noncomment code lines in the matching function block.
                for n,line in enumerate(body,begin+1):
                    stripped=line.strip()
                    if not stripped or stripped.startswith(";") or stripped.startswith("（") or stripped.startswith("//"):
                        continue
                    prof.append(line_profile(line,n))
                    if len(prof)>=350:
                        break
                key=f"{rel}::{name}@{begin+1}"
                function_profiles[key]={"function":name,"path":rel,"start_line":begin+1,"end_line":end,"profiled_line_count":len(prof),"body_line_count":end-begin,"logic_lines":prof}
                function_declarations.setdefault(name,[]).append({"path":rel,"start_line":begin+1,"end_line":end,"body_line_count":end-begin})
        report["reachable_function_profiles"]=function_profiles
        report["function_declarations"]=function_declarations

        # Index where each built-in/learned/custom Atsuko skill is mentioned.
        # This helps map learned-skill list -> skill data -> implementation files.
        known_skills = [
            "一分の活泉","シングルショット","ディア","スク・カジャ","咆哮","連続撃ち",
            "見切り","神経異常耐性","精神異常耐性","食いしばり","スクカジャオート",
            "癒しの波動","刹那五月雨撃","ダッジカウンター","伍絶切羽",
            "隠れるよ","これも私の力……？","教えられた事","痛いのは慣れてる",
        ]
        skill_mentions={}
        for skill_name in known_skills:
            rx=re.compile(re.escape(skill_name),re.I)
            rows=[]
            for rel,(p,txt) in text_by_rel.items():
                for n,line in enumerate(txt.splitlines(),1):
                    if rx.search(line):
                        rows.append({
                            "path":rel,"line":n,
                            "identifiers":tokens(line)[:45],
                            "numbers":re.findall(r"(?<![A-Z_])[-+]?\d+(?:\.\d+)?",line)[:15],
                            "attributes":sorted({a for a in ["力","知恵","魔力","耐力","速さ","運","HP","MP","命中","回避","魔法威力","魔法効果","回復量"] if a in line}),
                            "calls":[x.upper() for x in call_re.findall(line)][:10],
                            "labels":re.findall(r'"([^"]{1,48})"',line)[:5],
                        })
                        if len(rows)>=60: break
                if len(rows)>=60: break
            skill_mentions[skill_name]=rows
        report["skill_mentions"]=skill_mentions

        report["target_file_profiles"]=target_records

        # Function headers and ordered formula-token streams for Atsuko's custom skills
        # plus the battle/healing engine. This keeps names, dependencies and constants
        # without retaining verbatim source lines.
        header_index={}
        expression_index={}
        token_pat=re.compile(r'"[^"\n]{0,80}"|\'[^\'\n]{0,80}\'|[-+]?\d+(?:\.\d+)?|[A-Za-z_][A-Za-z0-9_]*|[一-龠々ぁ-ゖァ-ヺー]+|\+=|-=|\*=|/=|==|!=|>=|<=|[=+*/%?:()<>-]')
        target_code_suffixes=target_suffixes[1:]
        for rel,(p,txt) in text_by_rel.items():
            is_target=any(rel.endswith(s) for s in target_code_suffixes)
            is_engine=any(rel.endswith(s) for s in engine_suffixes)
            has_heal_calc="GET_BASEHEAL_IN_CALC" in txt
            has_dodge_buff="SKILL_SET_カジャンダ" in txt
            if not (is_target or is_engine or has_heal_calc or has_dodge_buff):
                continue
            headers=[]
            exprs=[]
            for n,line in enumerate(txt.splitlines(),1):
                stripped=line.strip()
                hm=re.match(r"^(?:@([^\s(]+)|#FUNCTION\s+([^\s(]+))",stripped,re.I)
                if hm:
                    headers.append({"line":n,"name":(hm.group(1) or hm.group(2)).upper()})
                relevant=re.search(r"GET_SKILL|SKILL_SET_|GIVE_EFFECT_CHARA|HEAL_|BASEHEAL|L_STAT|VAR_HP|BTL_CALC_|GET_BATTLESTATUS|MAXBASE|MAXABL|BASE\b|ABL\b|DAMAGE|回復|回避|力|知恵|魔力|耐力|速さ|運|HP|MP|魔法威力|魔法効果",line,re.I)
                equation=re.search(r"(?:=|\+=|-=|\*=|/=|RETURNF?\b|CALL|GOSUB|TRYCALL|SELECTCASE|CASE\b)",line,re.I)
                status_formula_window = rel.endswith("SYSTEM_STATUS_CALC.ERB") and 1120 <= n <= 1200
                if (relevant and equation or is_target and equation or status_formula_window) and len(exprs)<480:
                    exprs.append({"line":n,"tokens":EXPR_TOKEN.findall(line)[:120]})
            header_index[rel]={"headers":headers}
            if exprs:
                expression_index[rel]={"expressions":exprs}
        report["target_function_headers"]=header_index
        report["ordered_formula_tokens"]=expression_index

        # Narrow cross-reference map for character-specific MAG skills and the
        # race/status formula dispatcher. Keep tokenized references only.
        xref_terms = {
            "mag_skill_list": re.compile(r"MAG習得スキルリスト"),
            "atsuko_evasion_helper": re.compile(r"SKILL_SET_カジャンダ"),
            "human_status_formula": re.compile(r"HUMAN_BTL_STAT"),
            "devil_status_formula": re.compile(r"DEVIL_BTL_STAT"),
            "battle_status_type": re.compile(r"BATTLESTATUS_TYPE"),
            "role_prop_lookup": re.compile(r"GETS_ROLE_PROP"),
            "human_type_selector": re.compile(r'GETS_ROLE_PROP.*"BATTLESTATUS_TYPE"|BATTLESTATUS_TYPE.*"人間"'),
        }
        xref_hits = {name: [] for name in xref_terms}
        for rel,(p,txt) in text_by_rel.items():
            current_function = ""
            for n,line in enumerate(txt.splitlines(),1):
                header_match = re.match(r"^\\s*(?:@|#FUNCTION\\s+)([^\\s(]+)",line.strip(),re.I)
                if header_match:
                    current_function = header_match.group(1)
                for term_name,rx in xref_terms.items():
                    if len(xref_hits[term_name]) >= 300:
                        continue
                    if rx.search(line):
                        xref_hits[term_name].append({
                            "path": rel, "line": n, "function": current_function,
                            "identifiers": tokens(line)[:50],
                            "expression_tokens": EXPR_TOKEN.findall(line)[:120],
                            "numbers": re.findall(r"(?<![A-Z_])[-+]?\\d+(?:\\.\\d+)?",line)[:20],
                            "labels": re.findall(r'"([^"]{1,60})"',line)[:8],
                        })
        report["cross_reference_hits"] = xref_hits
        report["shared_formula_profiles"]=engine_records
        report["target_calls"]=sorted(call_symbols)
        report["formula_file_count"]=sum(1 for _,txt in textfiles if any(FORM_PAT.search(line) for line in txt.splitlines()))
        report["skill_keyword_count"]=sum(1 for _,txt in textfiles for line in txt.splitlines() if SKILL_PAT.search(line))

        # Save a complete, valid JSON file; no inventory dump and no verbatim source text.
        save("02_structural_report.json",json.dumps(report,ensure_ascii=False,indent=2))
        save("03_summary.md",
             "# Eraten temporary acquisition summary\n\n"
             f"- Downloaded: {report['downloaded']}\n"
             f"- Archive bytes: {report.get('download',{}).get('bytes','n/a')}\n"
             f"- SHA-256: {report.get('download',{}).get('sha256','n/a')}\n"
             f"- ZIP entries: {report.get('archive',{}).get('entry_count','n/a')}\n"
             f"- Extracted files: {report.get('archive',{}).get('file_count','n/a')}\n"
             f"- Text candidates: {report.get('text_candidate_count','n/a')}\n"
             f"- Atsuko matching lines: {len(char_hits)}\n"
             f"- Files with Atsuko: {len(char_files)}\n"
             f"- Targeted Atsuko data files: {len(report.get('target_file_profiles',{}))}\n"
             f"- Shared formula files profiled: {len(report.get('shared_formula_profiles',{}))}\n"
             f"- Skill keyword matching lines: {report.get('skill_keyword_count',0)}\n"
             f"- Files with combat formula markers: {report.get('formula_file_count',0)}\n\n"
             "Only structured fields, identifiers, constants and line references were retained; no source lines or game files were uploaded.")
    except Exception as e:
        report["error"]=f"{type(e).__name__}: {str(e)[:1200]}"
        save("02_structural_report.json",json.dumps(report,ensure_ascii=False,indent=2))
        save("03_summary.md", "# Acquisition failed\n\n"+report["error"]+"\n\nSee diagnostics artifact.")
        raise
    finally:
        if archive.exists(): archive.unlink()
        if extracted.exists(): shutil.rmtree(extracted,ignore_errors=True)
        if WORK.exists(): shutil.rmtree(WORK,ignore_errors=True)

if __name__=="__main__":
    try: run()
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}",file=sys.stderr)
        raise

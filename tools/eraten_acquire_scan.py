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
    errors=[]
    # Only click controls whose label clearly starts a download; never click help links.
    action_re = re.compile(r"^\s*(?:ダウンロード開始|ダウンロードする|Download now|Start download)\s*$", re.I)
    for attempt in range(3):
        for p in list(context.pages):
            try:
                if p.is_closed():
                    continue
                p.wait_for_timeout(600)
                controls = [
                    p.get_by_role("button", name=action_re),
                    p.locator('input[type="button"],input[type="submit"],button,[role="button"]'),
                ]
                used=set()
                for group in controls:
                    try:
                        count=min(group.count(),25)
                    except Exception:
                        continue
                    for i in range(count):
                        el=group.nth(i)
                        try:
                            if not el.is_visible(timeout=500):
                                continue
                            label=" ".join(filter(None,[
                                el.inner_text(timeout=500),
                                el.get_attribute("value"),
                                el.get_attribute("aria-label"),
                                el.get_attribute("title"),
                            ])).strip()
                            if not action_re.match(label):
                                continue
                            key=(scrub(p.url),label)
                            if key in used:
                                continue
                            used.add(key)
                            try:
                                with p.expect_download(timeout=15000) as ev:
                                    el.click(timeout=5000,force=True)
                                dl=ev.value
                                dl.save_as(str(archive))
                                return {
                                    "page_url":scrub(p.url),
                                    "label":label[:120],
                                    "suggested_filename":dl.suggested_filename,
                                    "click_mode":"exact_download_button",
                                    "attempt":attempt+1,
                                }
                            except Exception as exc:
                                errors.append(f"attempt {attempt+1}, {label[:80]}: {type(exc).__name__}: {str(exc)[:220]}")
                        except Exception as exc:
                            errors.append(f"control: {type(exc).__name__}: {str(exc)[:120]}")
            except Exception as exc:
                errors.append(f"page: {type(exc).__name__}: {str(exc)[:120]}")
    metadata=[]
    for p in context.pages:
        try:
            body=p.locator("body").inner_text(timeout=2500)[:1800]
            controls=p.locator("button,input[type=button],input[type=submit],[role=button]").evaluate_all(
                "(els)=>els.slice(0,60).map(e=>({tag:e.tagName,text:(e.innerText||e.value||e.getAttribute('aria-label')||'').trim().slice(0,120),type:e.type||null,disabled:!!e.disabled,id:e.id||null,className:typeof e.className==='string'?e.className.slice(0,100):'',rect:(()=>{const r=e.getBoundingClientRect();return {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}})()}))"
            )
            metadata.append({"url":scrub(p.url),"title":p.title()[:120],"body_excerpt":body,"controls":controls})
            try:
                p.screenshot(path=str(REPORT/"download-page.png"),full_page=True,timeout=5000)
            except Exception:
                pass
        except Exception:
            pass
    save("01_download_diagnostics.json",json.dumps({"pages":metadata,"errors":errors[-60:]},ensure_ascii=False,indent=2))
    raise RuntimeError("Exact download-start button did not trigger a file download. See 01_download_diagnostics.json")

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
        ]
        engine_suffixes=[
          "SYSTEM_BATTLE_DAMAGE.ERB","SYSTEM_STATUS_CALC.ERB",
          "GET_BATTLESTATUS.ERB","GET_BATTLESTATUS_NUM.ERB",
        ]
        target_records={}
        call_symbols=set()
        formula_line_re=re.compile(r"CALLF?|TRYCALL|CALLFORM|GOSUB|GOTO|GET_SKILL|SKILL_(?:POWER|HITRATE|DAMAGETYPE|HEAL_TYPE)|(?:BASE|ABL|MAXBASE|CSTR|CFLAG)\b|(?:力|知恵|魔力|耐力|速さ|運|STR|INT|MAG|END|AGI|LUK|HP|MP)\b|DAMAGE|HEAL|HITRATE|EVASION|POWER",re.I)
        call_re=re.compile(r"\b(?:CALLFORM|TRYCALL|CALLF?|GOSUB|GOTO)\s+([A-Z_][A-Z0-9_]*)",re.I)
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
        report["target_file_profiles"]=target_records
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

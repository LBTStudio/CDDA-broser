#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, os, re, shutil, sys, time, zipfile
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
    pages_seen=[]
    for _ in range(5):
        for p in list(context.pages):
            if p not in pages_seen:
                pages_seen.append(p)
            try:
                if p.is_closed(): continue
                p.wait_for_timeout(1500)
                # GigaFile normally exposes a visible individual-download button.
                locators=[
                    p.get_by_role("button", name=re.compile(r"ダウンロード|download",re.I)),
                    p.get_by_role("link", name=re.compile(r"ダウンロード|download",re.I)),
                    p.locator("input[type=button],input[type=submit],button,a[download],[role=button]"),
                ]
                tried=set()
                for locator in locators:
                    try: count=min(locator.count(),100)
                    except Exception: continue
                    for i in range(count):
                        el=locator.nth(i)
                        try:
                            if not el.is_visible(timeout=500): continue
                            label=" ".join(filter(None,[el.inner_text(timeout=500),el.get_attribute("value"),el.get_attribute("aria-label"),el.get_attribute("title")])).strip()
                            href=el.get_attribute("href") or ""
                            combined=(label+" "+href).strip()
                            if not re.search(r"ダウンロード|download|個別|まとめる|download\.php|download",combined,re.I): continue
                            key=(p.url, label, href)
                            if key in tried: continue
                            tried.add(key)
                            try:
                                with p.expect_download(timeout=8000) as ev:
                                    el.click(timeout=5000)
                                dl=ev.value
                                dl.save_as(str(archive))
                                return {"page_url":scrub(p.url),"label":label[:120],"suggested_filename":dl.suggested_filename,"click_mode":"download_event"}
                            except Exception as exc:
                                errors.append(f"{label[:80]} -> {type(exc).__name__}: {str(exc)[:130]}")
                        except Exception as exc:
                            errors.append(f"control -> {type(exc).__name__}: {str(exc)[:100]}")
                # If a page opened a confirmation tab, next loop inspects it.
                if len(context.pages)>len(pages_seen):
                    pages_seen.extend([q for q in context.pages if q not in pages_seen])
            except Exception as exc:
                errors.append(f"page -> {type(exc).__name__}: {str(exc)[:100]}")
    metadata=[]
    for p in context.pages:
        try:
            body=p.locator("body").inner_text(timeout=2500)[:1800]
            metadata.append({"url":scrub(p.url),"title":p.title()[:120],"body_excerpt":body,"buttons":[x[:100] for x in p.locator("button,input[type=button],input[type=submit],a[download]").evaluate_all("(els)=>els.slice(0,60).map(e=>(e.innerText||e.value||e.getAttribute('aria-label')||'').trim())")]})
            try: p.screenshot(path=str(REPORT/"download-page.png"),full_page=True,timeout=5000)
            except Exception: pass
        except Exception: pass
    save("01_download_diagnostics.json",json.dumps({"pages":metadata,"errors":errors[-80:]},ensure_ascii=False,indent=2))
    raise RuntimeError("Normal visible download control did not start a file download. See 01_download_diagnostics.json")

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
        # No raw source lines or game files are put in the public artifact. Emit structure only.
        char_hits=[]
        char_files=set()
        for p,txt in textfiles:
            for n,line in enumerate(txt.splitlines(),1):
                if CHAR_PAT.search(line):
                    char_files.add(p.relative_to(extracted).as_posix())
                    char_hits.append({"path":p.relative_to(extracted).as_posix(),"line":n,"terms":sorted(set(CHAR_PAT.findall(line))),"ids":sorted(set(NUMID.findall(line))),"identifiers":tokens(line),"attribute_tokens":[a for a in ATTRS if a.lower() in line.lower()]})
                    if len(char_hits)>=500: break
            if len(char_hits)>=500: break
        report["character_hits"]=char_hits
        report["character_files"]=sorted(char_files)
        skill_hits=[]
        formula_by_file={}
        for p,txt in textfiles:
            rel=p.relative_to(extracted).as_posix()
            lines=txt.splitlines()
            sk=0
            formulas=set()
            symbols=set()
            for n,line in enumerate(lines,1):
                if SKILL_PAT.search(line):
                    sk+=1
                    if len(skill_hits)<700:
                        skill_hits.append({"path":rel,"line":n,"terms":sorted(set(SKILL_PAT.findall(line))),"ids":sorted(set(NUMID.findall(line))),"identifiers":tokens(line),"quoted_names":re.findall(r'"([^"]{2,40})"',line)[:8]})
                if FORM_PAT.search(line):
                    formulas.update(FORM_PAT.findall(line))
                    symbols.update(tokens(line))
                    symbols.update(a for a in ATTRS if a.lower() in line.lower())
            if formulas:
                formula_by_file[rel]={"formula_terms":sorted(set(x.lower() for x in formulas)),"identifiers":sorted(symbols)[:100],"line_count":sum(1 for line in lines if FORM_PAT.search(line))}
        report["skill_hits"]=skill_hits
        report["formula_files"]=formula_by_file
        report["inventory"]=inventory[:30000]
        # keep output bounded; no source text is included.
        save("02_structural_report.json",json.dumps(report,ensure_ascii=False,indent=2)[:3_000_000])
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
             f"- Skill-related matches: {len(skill_hits)}\n"
             f"- Files with combat formula markers: {len(formula_by_file)}\n\n"
             "Only a structural index was retained; the ZIP, extracted data, and raw source lines were not uploaded.")
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

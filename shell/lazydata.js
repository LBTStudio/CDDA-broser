/* CDDA-BROSER: 追加データパックの遅延マウント
 * ==================================================================
 * なぜこのファイルが存在するのか
 * ==================================================================
 * 起動時にメインメニューまでに読まれるのは、パッケージ全体の
 * 25% 程度（実測: 5,892 ファイル中 74 ファイル / 134.6MB 中 33.6MB）で、
 * 残りの大半は /data/json（世界を開始するまで一度も読まれない）と
 * 未選択 tileset の画像である。
 *
 * そこで配布物を 4 種類のパックに分ける:
 *   initial          : core + locale + tileset メタ + 既定 tileset の画像
 *   world            : /data/json + /data/mods の実データ
 *   gfx-<tileset>    : 各 tileset の画像
 * これらは file_packager --lz4 が生成した「素の」パックで、ゲーム本体は
 * 一切改造されていない。差し込みは Emscripten の LZ4 ローダー
 * (Module.LZ4.loadPackage) を実行後に呼ぶだけで成立する。
 *
 * ==================================================================
 * 何を保証するのか
 * ==================================================================
 * ・パックが 1 つも無い配布物（従来の単一 .data）では完全に無効化される。
 *   manifest.json が 404 なら以降は何もしない = 従来どおり動く。
 * ・マウントは「読み込みが失敗したら再試行できる」形にしてある。
 *   Asyncify 側の呼び出し元は Promise を待っているので、再試行後に
 *   そのまま処理が続く（プロセスの作り直しが不要）。
 * ・マウント完了の判定は Module.LZ4.loadPackage の同期呼び出しを包んで
 *   取る。生成ローダーの addRunDependency は実行後だと当てにならない。
 */
(function () {
  "use strict";

  /* このスクリプトは本体バンドルより先に読み込まれる。Emscripten の
     バンドルは `var Module = typeof Module != 'undefined' ? Module : {}`
     で既存の器を再利用するので、ここで先に作っておけば
     読み込み順に依存しなくなる。 */
  window.Module = window.Module || {};

  var PACK_DIR = "packs/";
  var MANIFEST_URL = PACK_DIR + "manifest.json";

  var api = null;          // index.html から注入される
  var manifest;            // null = 分割なし
  var manifestTried = false;
  var mounted = Object.create(null);   // name -> {bytes, ms}
  var pending = Object.create(null);   // name -> Promise
  var prefetched = Object.create(null);

  function init(a) {
    api = a;
  }

  /* ---- manifest: 分割ビルドかどうかの判定を兼ねる ---------------- */
  function getManifest() {
    if (manifestTried) return Promise.resolve(manifest);
    manifestTried = true;
    var url = MANIFEST_URL;
    return fetch(url, { cache: "no-cache" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (j && Array.isArray(j.packs)) manifest = j;
        return manifest;
      })
      .catch(function () { return null; });
  }

  function has(name) {
    return !!(manifest && manifest.packs && manifest.packs.indexOf(name) >= 0);
  }

  /* ---- 要求 → パック名 ------------------------------------------ */
  // spec は「パック名」そのもの、または C++ から渡されるパス / tileset id。
  //
  // 【先頭スラッシュの正規化が必須な理由】
  // C++ 側は cata_path::get_unrelative_path() を渡す。これは
  // "data/json" のように【先頭スラッシュの無い】相対パスであり、
  // 仮想 FS 上の絶対パス "/data/json" ではない。ここを取りこぼすと
  // packFor が null を返し、world パックが一度も差し込まれないまま
  // 「空のディレクトリを静かに走査する」= データ欠落のまま起動する。
  function packFor(spec) {
    if (!spec) return null;
    var s = String(spec).trim();
    if (has(s)) return s;                       // パック名をそのまま渡された場合
    if (s === "world") return has("world") ? "world" : null;
    if (s.indexOf("gfx:") === 0) {
      var name = "gfx-" + s.slice(4).trim();
      return has(name) ? name : null;
    }
    if (s.charAt(0) !== "/") s = "/" + s;
    while (s.indexOf("//") >= 0) s = s.split("//").join("/");
    if (s === "/data/json" || s.indexOf("/data/json/") === 0 ||
        s === "/data/mods" || s.indexOf("/data/mods/") === 0) {
      return has("world") ? "world" : null;
    }
    return null;
  }

  /* ---- 1 パックの取得と差し込み --------------------------------- */
  async function mountPack(name) {
    var buf = await api.cachedAssetResponse(PACK_DIR + name + ".data", "追加データ")
      .then(function (r) { return r.arrayBuffer(); });

    var t0 = (typeof performance !== "undefined" ? performance.now() : Date.now());
    var LZ4 = Module["LZ4"];
    var origLoad = LZ4.loadPackage;
    var prevGPP = Module["getPreloadedPackage"];
    var handed = false;
    var settle = null;
    var mountedPromise = new Promise(function (res) { settle = res; });

    /* 生成ローダーは「取得済みバッファがあればそれを使う」フックを
       先に問い合わせる。ここで 1 回だけ手渡し、ネットワーク往復を
       二重に発生させない。 */
    Module["getPreloadedPackage"] = function () {
      if (handed) return null;
      handed = true;
      return buf;
    };
    LZ4.loadPackage = function (pack, preload) {
      var out = origLoad.call(this, pack, preload);
      if (settle) { settle(); settle = null; }
      return out;
    };

    try {
      await api.loadScript(PACK_DIR + name + ".js");
      await Promise.race([
        mountedPromise,
        new Promise(function (_, rej) {
          setTimeout(function () { rej(new Error("パックの展開が完了しませんでした: " + name)); }, 20000);
        }),
      ]);
    } finally {
      LZ4.loadPackage = origLoad;
      if (prevGPP === undefined) delete Module["getPreloadedPackage"];
      else Module["getPreloadedPackage"] = prevGPP;
    }

    var now = (typeof performance !== "undefined" ? performance.now() : Date.now());
    mounted[name] = { bytes: buf.byteLength, ms: Math.round(now - t0) };
    console.log("[packs] mounted " + name + " (" + (buf.byteLength / 1048576).toFixed(1) + "MB, " + mounted[name].ms + "ms)");
    return mounted[name];
  }

  /* 失敗したら UI で待つ。再試行でそのまま再開できる。 */
  async function mountWithRetry(name) {
    for (;;) {
      try {
        return await mountPack(name);
      } catch (e) {
        console.warn("[packs] mount failed: " + name, e);
        /*
         * ここで例外を投げ返すと wasm 側の呼び出し元が中断し、
         * 中途半端な状態で世界読み込みが進んでしまう。
         * ユーザーが再試行を選ぶまで待つのが正しい。
         */
        await api.onMountError(name, e);
      }
    }
  }

  /* ---- 公開: 読み込み前に必ず呼ばれる ---------------------------- */
  async function ensure(spec) {
    var m = await getManifest();
    if (!m) return true;                 // 分割なし配布物
    var name = packFor(spec);
    if (!name || mounted[name]) return true;
    if (!pending[name]) {
      pending[name] = mountWithRetry(name).then(function (r) {
        delete pending[name];
        return r;
      }, function (e) {
        delete pending[name];
        throw e;
      });
    }
    await pending[name];
    return true;
  }

  /* ---- 公開: 先読み（メニュー表示後にバックグラウンドで） -------- */
  /* Cache Storage に温めるだけで、ヒープには保持しない。
     実際のマウントは ensure() が呼ばれた瞬間に行う。 */
  async function prefetch(names) {
    var m = await getManifest();
    if (!m) return;
    var list = Array.isArray(names) ? names : [names];
    for (var i = 0; i < list.length; i++) {
      var name = list[i];
      if (!has(name) || prefetched[name]) continue;
      prefetched[name] = true;
      try {
        var res = await api.cachedAssetResponse(PACK_DIR + name + ".data", "先読み");
        /* 本文は読み捨てる（Cache Storage 側に残る）。 */
        if (res && res.body && res.body.cancel) { try { await res.body.cancel(); } catch (e) { /* noop */ } }
      } catch (e) {
        prefetched[name] = false;
        console.warn("[packs] prefetch failed: " + name, e);
      }
    }
  }

  /* ---- 公開: メニュー表示後の下ごしらえ --------------------------
     manifest.eager が false でなければ「フックが無いビルド」とみなし、
     世界パックを先に差し込んでおく（読み込み時に空ディレクトリを
     静かに読んでしまう事故を防ぐ）。フックがあるビルドでは
     eager:false になるので、必要になるまで 1 バイトも取りに行かない。 */
  async function warmup() {
    var m = await getManifest();
    if (!m) return;
    await prefetch(["world"]);
    if (m.eager === false) return;
    if (!has("world") || mounted["world"]) return;
    try {
      await ensure("world");
      console.log("[packs] eager mount (このビルドには読み込み前フックがありません)");
    } catch (e) {
      console.warn("[packs] eager mount failed", e);
    }
  }

  /* ---- 診断: 未マウントのまま読まれたら記録する ------------------ */
  /* フック漏れ（= 空ディレクトリを読んで静かに壊れる）を検出する。
     挙動は変えず、警告だけ出す。 */
  function watchLazyPaths() {
    var FS = Module["FS"];
    if (!FS || FS.__cddaPackWatch) return;
    FS.__cddaPackWatch = true;
    var origOpen = FS.open;
    FS.open = function (path, flags, mode) {
      try {
        var p = String(path);
        if ((p.indexOf("/data/json") === 0 || p.indexOf("/data/mods") === 0) && !mounted["world"]) {
          console.warn("[packs] world パック未マウントで読み取り: " + p);
        }
      } catch (e) { /* noop */ }
      return origOpen.call(this, path, flags, mode);
    };
  }

  /* UI に出す名前。パック名をそのまま見せない。 */
  function displayName(name) {
    var n = String(name);
    if (n === "world") return "世界データ";
    if (n.indexOf("gfx-") === 0) return "タイル画像 (" + n.slice(4) + ")";
    return n;
  }
  window.CddaLazyPacks = {
    init: init,
    ensure: ensure,
    prefetch: prefetch,
    warmup: warmup,
    watchLazyPaths: watchLazyPaths,
    displayName: displayName,
    get mounted() { return mounted; },
    get manifest() { return manifest; },
  };

  /* Asyncify からはこの名前で呼ぶ（パッチ側の cata_web_ensure_pack）。 */
  Module["cddaEnsurePack"] = ensure;
})();

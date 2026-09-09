import QtQuick
import Quickshell
import Quickshell.Io

// Headless service: stream the Omarchy GIF to a SteelSeries Apex OLED.
// apply.py watches hidraw. The bar widget reads status from this singleton.
Item {
  id: root

  property var manifest: null
  property var shell: null
  property bool stopping: false
  property bool enabled: true
  property bool settingsLoaded: false
  property string preset: "omarchy"
  property bool hasCustom: false
  readonly property bool isCustom: preset === "custom"
  readonly property bool isStatic: preset === "static"
  readonly property bool isSpin: preset === "spin"
  readonly property bool isWaves: preset === "waves"
  readonly property bool isText: preset === "text"

  // Custom typed text, rendered with the same typewriter/static/spin/waves
  // styles as the bundled Omarchy wordmark (see apply.py's render_text()).
  property string customText: ""
  property string textStyle: "typewriter"
  property string textFont: "jetbrains"
  property int textDelayMs: 100
  property bool hasText: false
  readonly property var textStyleOrder: ["typewriter", "static", "spin", "waves"]
  readonly property var textFontOrder: ["jetbrains", "omarchy"]
  readonly property var textFontLabels: ({ "jetbrains": "JetBrains Mono", "omarchy": "Omarchy Block" })
  // Which kind of result importProcess is currently producing, so its single
  // onExited handler knows whether to land on the "custom" or "text" preset.
  property string pendingKind: "custom"

  property bool invert: false
  property int threshold: 50
  property int delayMs: 100
  property string sourceLabel: "Omarchy"
  property string sourceFile: ""
  // Per-kind preview revision, bumped only when that kind is actually
  // (re)rendered — not on a plain preset switch, since the file for the
  // current revision is still perfectly valid then. See preview_paths() in
  // apply.py and customPreview/textPreview below.
  property int customPreviewRev: 0
  property int textPreviewRev: 0
  property int pendingPreviewSuffix: 0
  property bool importBusy: false
  property bool pickBusy: false
  property int webGifIndex: -1
  // Gifs on nlog.us's SteelSeries OLED gif page (../content/steelseries/<file>),
  // renamed by hand from their imgur hashes and sorted for a sane cycle order.
  // Delete any entry here to drop it from the "grab a gif" cycle. Entries with
  // `path` instead of `file` are imported straight from a bundled asset file
  // (ships with the plugin) rather than fetched from nlog.us.
  readonly property var webGifs: [
    { path: pluginDir + "/assets/stickfight.gif", name: 'Stick Fight' },
    { path: pluginDir + "/assets/nightrunner.gif", name: 'Night Runner' },
    { file: "vaNQK2n.gif", name: 'Arch Stripes' },
    { file: "2TP0MFR.gif", name: 'Bad' },
    { file: "t8IJvJi.gif", name: 'Be Free' },
    { file: "3pdYDmI.gif", name: 'Blank Fade' },
    { file: "WnHOHQG.gif", name: 'Blink Dots' },
    { file: "eoimUey.gif", name: 'Boom' },
    { file: "9Mf0FTR.gif", name: 'Castle Sparkle' },
    { file: "dIjeOFq.gif", name: 'Bongo Cat' },
    { file: "c0BnYJc.gif", name: 'Cat Walk' },
    { file: "8Vhseur.gif", name: 'Cheshire Grin' },
    { file: "87SFGzU.gif", name: 'Crescent Moon' },
    { file: "vrvbSoS.gif", name: 'Curl Swirl' },
    { file: "62LWpGW.gif", name: 'Doodle Creature' },
    { file: "Lkc25Tp.gif", name: 'Eye' },
    { file: "eTQFQ30.gif", name: 'Fade Out' },
    { file: "CQjFPee.gif", name: 'Figure Sketch' },
    { file: "RPUf7R8.gif", name: 'Fuzzy Orb' },
    { file: "8AlisMe.gif", name: 'Ghost' },
    { file: "SjK1UNF.gif", name: 'Glitch Grid' },
    { file: "xKCwq7P.gif", name: 'Glitch Stripes' },
    { file: "OrHjqAB.gif", name: 'Gone' },
    { file: "2XENnwN.gif", name: 'Google It' },
    { file: "Wp9kcdN.gif", name: 'Half Moon' },
    { file: "uuuX9AD.gif", name: 'Heart' },
    { file: "eCqylDb.gif", name: 'Hero Icons' , delayScale: 2 },
    { file: "hQ0YH0C.gif", name: 'Honeycomb' },
    { file: "NKuoo8B.gif", name: 'Hook Arc' },
    { file: "lpUt5va.gif", name: 'Hypno Swirl' },
    { file: "KkzXklj.gif", name: "I Don't Need You" , delayScale: 2 },
    { file: "Kc954CN.gif", name: 'I Love You' },
    { file: "XtEnXuP.gif", name: 'Incline Rest' },
    { file: "V338gWl.gif", name: 'Labs' },
    { file: "JQxBkTD.gif", name: 'Loading Dots' },
    { file: "bAgUQ6E.gif", name: 'Optical Rings' },
    { file: "qh7IJt7.gif", name: 'PC Master Race' },
    { file: "8mtMros.gif", name: 'Paper Airplane' },
    { file: "5yZGqLK.gif", name: 'Polka Dots' },
    { file: "FQmwbkd.gif", name: 'Reclining Figure' },
    { file: "bJPgpEm.gif", name: 'Revolver Hand' },
    { file: "v5Infnc.gif", name: 'Rude Stack' },
    { file: "A4pFWMK.gif", name: 'Scratch Marks' },
    { file: "kMu9kfg.gif", name: 'Smoke Fade' },
    { file: "FvJiQKE.gif", name: 'Sound Waves' },
    { file: "qLb6Dg0.gif", name: 'Spark Burst' },
    { file: "0qklJOz.gif", name: 'Squiggle Sketch' },
    { file: "AGFUdgz.gif", name: 'Start Screen' },
    { file: "U8V5hMB.gif", name: 'Swirl Mark' },
    { file: "MkxpfgN.gif", name: 'The End' },
    { file: "yaMRvBI.gif", name: 'Walk The Dog' },
    { file: "tzgjo4a.gif", name: 'Wave Line' },
    { file: "pKFkBUl.gif", name: 'White Screen' }
  ]
  property bool keepDelayOnImport: false

  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string stateHome: Quickshell.env("XDG_STATE_HOME") || (home + "/.local/state")
  readonly property string settingsPath: stateHome + "/omarchy/steelseries-oled.json"
  readonly property string customDir: stateHome + "/omarchy/steelseries-oled"
  readonly property string customFrames: customDir + "/custom.frames"
  readonly property string customRest: customDir + "/custom.bin"
  // Rev-stamped so every revision is a genuinely new file — Qt's AnimatedImage
  // pixmap cache can serve a stale frame if the same path gets overwritten
  // in place, even with a cache-busting URL fragment (apply.py prunes the
  // previous rev's files when it writes a new one).
  readonly property string customPreview: customDir + "/preview-" + customPreviewRev + ".gif"
  readonly property string customSource: customDir + "/" + (sourceFile !== "" ? sourceFile : "source.gif")
  readonly property string textFrames: customDir + "/text.frames"
  readonly property string textRest: customDir + "/text.bin"
  readonly property string textPreview: customDir + "/text-preview-" + textPreviewRev + ".gif"

  readonly property string pluginDir: {
    var url = String(Qt.resolvedUrl("."))
    if (url.indexOf("file://") === 0)
      url = url.slice(7)
    if (url.length && url.charAt(url.length - 1) === "/")
      url = url.slice(0, url.length - 1)
    return url
  }
  readonly property string helper: pluginDir + "/apply.py"
  readonly property string udevDest: "/etc/udev/rules.d/71-steelseries-apex-oled.rules"
  // Exact bytes of udev/71-steelseries-apex-oled.rules. pkexec writes these
  // from argv; the plugin tree is never opened as root.
  readonly property string udevRuleHex: "2320537465656c5365726965732041706578204f4c4544202850726f202f2037202f20352c2066756c6c2d73697a6520616e6420544b4c292e0a23205441472b3d227561636365737322206c65747320746865207365617465642075736572207772697465207468652076656e646f7220686964726177206e6f646520776974686f75740a23206265696e6720696e2074686520696e7075742067726f75702e20546869732066696c65206973206e616d65642037312d20736f2037332d736561742d6c6174652e72756c65730a23207374696c6c206170706c696573207468652041434c3b20612039392d2072756c6520697320746f6f206c61746520616e6420616363657373206e65766572206c616e64732e0a2320546869732072756c6520646f6573206e6f742072756e20616e792070726f6772616d2e0a4b45524e454c3d3d226869647261772a222c2053554253595354454d3d3d22686964726177222c2041545452537b696456656e646f727d3d3d2231303338222c2041545452537b696450726f647563747d3d3d22313631307c313631327c313631347c313631387c31363163222c204d4f44453d2230363630222c2047524f55503d22696e707574222c205441472b3d2275616363657373220a"

  property string state: "starting"
  property string devicePath: ""
  property string lastError: ""
  property bool permission: true
  property bool udevBusy: false

  readonly property bool present: state === "looping" || state === "denied"
  readonly property bool looping: enabled && state === "looping"
  readonly property bool needsUdev: enabled && present && !permission
  readonly property string defaultPreview: pluginDir + "/assets/omarchy-oled-128x40.gif"
  readonly property string staticPreview: pluginDir + "/assets/omarchy-oled-128x40.png"
  readonly property string staticFrames: pluginDir + "/assets/omarchy-oled-static.frames"
  readonly property string spinPreview: pluginDir + "/assets/omarchy-oled-spin.gif"
  readonly property string spinFrames: pluginDir + "/assets/omarchy-oled-spin.frames"
  readonly property string wavesPreview: pluginDir + "/assets/omarchy-oled-waves.gif"
  readonly property string wavesFrames: pluginDir + "/assets/omarchy-oled-waves.frames"
  readonly property string bundledRest: pluginDir + "/assets/omarchy-oled-128x40.bin"
  readonly property var bundledOrder: ["omarchy", "static", "spin", "waves"]
  readonly property string previewUrl: {
    var path = defaultPreview
    if (isCustom) path = customPreview
    else if (isStatic) path = staticPreview
    else if (isSpin) path = spinPreview
    else if (isWaves) path = wavesPreview
    else if (isText) path = textPreview
    return "file://" + path
  }
  readonly property string statusLabel: {
    if (!enabled) return "Off"
    if (needsUdev) return "Needs access"
    if (looping) return "On"
    if (state === "disconnected") return "No keyboard"
    if (lastError !== "") return "Error"
    return "…"
  }

  function watchCommand() {
    var cmd = ["python3", "-u", helper, "--watch", "--delay-ms", String(delayMs)]
    if (invert) cmd.push("--invert")
    if (isCustom) {
      cmd.push("--frames", customFrames)
      cmd.push("--rest", customRest)
    } else if (isStatic) {
      cmd.push("--frames", staticFrames)
      cmd.push("--rest", bundledRest)
    } else if (isSpin) {
      cmd.push("--frames", spinFrames)
      cmd.push("--rest", bundledRest)
    } else if (isWaves) {
      cmd.push("--frames", wavesFrames)
      cmd.push("--rest", bundledRest)
    } else if (isText) {
      cmd.push("--frames", textFrames)
      cmd.push("--rest", textRest)
    }
    return cmd
  }

  function restCommand() {
    return ["python3", "-u", helper, "--release"]
  }

  function setEnabled(on) {
    on = !!on
    if (enabled === on && settingsLoaded) {
      if (on) startWatch()
      return
    }
    enabled = on
    lastError = ""
    if (settingsLoaded) scheduleSave()
    if (on) startWatch()
    else stopWatch()
  }

  function toggleEnabled() {
    setEnabled(!enabled)
  }

  function startWatch() {
    if (stopping || !enabled || pluginDir === "")
      return
    if (watchProcess.running)
      watchProcess.running = false
    state = "starting"
    watchProcess.command = watchCommand()
    watchProcess.running = true
  }

  function stopWatch() {
    restartTimer.stop()
    if (watchProcess.running)
      watchProcess.running = false
    state = "off"
    if (pluginDir === "" || restProcess.running)
      return
    restProcess.command = restCommand()
    restProcess.running = true
  }

  function pickImage() {
    if (pickProcess.running || importProcess.running) return
    lastError = ""
    pickBusy = true
    pickProcess.command = [
      "omarchy", "file", "select",
      "--title", "OLED image",
      "--extensions", "gif png jpg jpeg webp bmp"
    ]
    pickProcess.running = true
  }

  function importImage(path) {
    if (!path || importProcess.running || pluginDir === "") return
    pendingKind = "custom"
    pendingPreviewSuffix = customPreviewRev + 1
    lastError = ""
    importBusy = true
    importProcess.command = [
      "python3", "-u", helper, "--import", path,
      "--out-dir", customDir,
      "--threshold", String(threshold),
      "--preview-suffix", String(pendingPreviewSuffix)
    ]
    importProcess.running = true
  }

  function _startWebGifImport(idx) {
    webGifIndex = idx
    var item = webGifs[idx]
    pendingKind = "custom"
    pendingPreviewSuffix = customPreviewRev + 1
    lastError = ""
    importBusy = true
    var cmd = ["python3", "-u", helper]
    if (item.path) cmd.push("--import", item.path)
    else cmd.push("--import-url", "https://www.nlog.us/content/steelseries/" + item.file)
    cmd.push("--out-dir", customDir, "--threshold", String(threshold), "--label", item.name)
    cmd.push("--preview-suffix", String(pendingPreviewSuffix))
    if (item.delayScale) cmd.push("--delay-scale", String(item.delayScale))
    importProcess.command = cmd
    importProcess.running = true
  }

  function fetchWebGif() {
    if (pickProcess.running || importProcess.running || webGifs.length === 0) return
    _startWebGifImport((webGifIndex + 1) % webGifs.length)
  }

  function jumpToWebGif(idx) {
    if (pickProcess.running || importProcess.running) return
    if (idx < 0 || idx >= webGifs.length) return
    _startWebGifImport(idx)
  }

  function _renderText(text, style, font) {
    pendingKind = "text"
    pendingPreviewSuffix = textPreviewRev + 1
    lastError = ""
    importBusy = true
    importProcess.command = [
      "python3", "-u", helper, "--render-text", text,
      "--style", style,
      "--font", font,
      "--out-dir", customDir,
      "--preview-suffix", String(pendingPreviewSuffix)
    ]
    importProcess.running = true
  }

  function setCustomText(text) {
    text = String(text || "").trim()
    if (!text || pickProcess.running || importProcess.running) return
    _renderText(text, textStyle, textFont)
  }

  function cycleTextStyle() {
    if (!hasText || pickProcess.running || importProcess.running) return
    var idx = textStyleOrder.indexOf(textStyle)
    var next = textStyleOrder[(idx + 1) % textStyleOrder.length]
    _renderText(customText, next, textFont)
  }

  function setTextStyle(style) {
    if (!hasText || pickProcess.running || importProcess.running) return
    if (textStyleOrder.indexOf(style) === -1 || style === textStyle) return
    _renderText(customText, style, textFont)
  }

  function cycleTextFont() {
    if (!hasText || pickProcess.running || importProcess.running) return
    var idx = textFontOrder.indexOf(textFont)
    var next = textFontOrder[(idx + 1) % textFontOrder.length]
    _renderText(customText, textStyle, next)
  }

  function setTextFont(font) {
    if (!hasText || pickProcess.running || importProcess.running) return
    if (textFontOrder.indexOf(font) === -1 || font === textFont) return
    _renderText(customText, textStyle, font)
  }

  function useText() {
    if (!hasText) return
    setPreset("text")
  }

  function reimportSaved() {
    if (!hasCustom && sourceFile === "") return
    keepDelayOnImport = true
    importImage(customSource)
  }

  function setPreset(name) {
    if (name !== "omarchy" && name !== "static" && name !== "spin" && name !== "waves" && name !== "text" && name !== "custom")
      name = "omarchy"
    preset = name
    if (name === "spin") {
      sourceLabel = "Spin"
      delayMs = 70
    } else if (name === "waves") {
      sourceLabel = "Waves"
      delayMs = 60
    } else if (name === "static") {
      sourceLabel = "Static"
      delayMs = 100
    } else if (name === "omarchy") {
      sourceLabel = "Omarchy"
      delayMs = 100
    } else if (name === "text") {
      sourceLabel = customText || "Text"
      delayMs = textDelayMs
    }
    lastError = ""
    if (settingsLoaded) scheduleSave()
    if (enabled) startWatch()
    else setEnabled(true)
  }

  function resetDefault() {
    setPreset("omarchy")
  }

  function useSpin() {
    setPreset("spin")
  }

  function cycleBundled() {
    var i = bundledOrder.indexOf(preset)
    setPreset(bundledOrder[(i + 1) % bundledOrder.length])
  }

  function useCustom() {
    if (!hasCustom) return
    if (sourceLabel === "" || sourceLabel === "Omarchy" || sourceLabel === "Static" || sourceLabel === "Spin")
      sourceLabel = "Custom"
    setPreset("custom")
  }

  function setInvert(on) {
    on = !!on
    if (invert === on) return
    invert = on
    if (settingsLoaded) scheduleSave()
    if (enabled) startWatch()
  }

  function toggleInvert() {
    setInvert(!invert)
  }

  function setThreshold(value) {
    var next = Math.max(5, Math.min(95, Math.round(Number(value))))
    if (next === threshold) return
    threshold = next
    if (settingsLoaded) scheduleSave()
    thresholdTimer.restart()
  }

  function setDelayMs(value) {
    var next = Math.max(50, Math.min(500, Math.round(Number(value))))
    if (next === delayMs) return
    delayMs = next
    if (settingsLoaded) scheduleSave()
    if (enabled) startWatch()
  }

  function handleLine(line) {
    if (!line || !enabled) return
    var msg = null
    try { msg = JSON.parse(line) } catch (e) { return }
    if (!msg || typeof msg !== "object") return
    if (msg.type === "status") {
      state = String(msg.state || "")
      devicePath = String(msg.path || "")
      permission = state !== "denied"
      if (state !== "error") lastError = ""
      if (state === "error") lastError = String(msg.message || "hid error").slice(0, 200)
    }
  }

  function installUdev() {
    if (udevProcess.running) return
    udevBusy = true
    lastError = ""
    udevProcess.command = [
      "pkexec", "python3", "-c",
      "import os, pathlib, subprocess\n"
        + "rule = bytes.fromhex(" + JSON.stringify(udevRuleHex) + ")\n"
        + "dest = pathlib.Path('/etc/udev/rules.d/71-steelseries-apex-oled.rules')\n"
        + "tmp = dest.with_name('.71-steelseries-apex-oled.rules.tmp')\n"
        + "tmp.write_bytes(rule)\n"
        + "os.chmod(tmp, 0o644)\n"
        + "os.replace(tmp, dest)\n"
        + "for old in ('99-steelseries-apex-oled.rules', '99-steelseries-apex7-oled.rules'):\n"
        + "    p = pathlib.Path('/etc/udev/rules.d') / old\n"
        + "    if p.exists(): p.unlink()\n"
        + "subprocess.check_call(['udevadm', 'control', '--reload-rules'])\n"
        + "subprocess.check_call(['udevadm', 'trigger', '--action=add', '--subsystem-match=hidraw'])\n"
        + "subprocess.check_call(['udevadm', 'trigger', '--action=change', '--subsystem-match=hidraw'])\n"
    ]
    udevProcess.running = true
  }

  function loadSettings(raw) {
    if (settingsLoaded) return
    var on = true
    var nextPreset = "omarchy"
    var label = "Omarchy"
    var inv = false
    var thr = 50
    var delay = 100
    var srcFile = ""
    var txt = ""
    var txtStyle = "typewriter"
    var txtFont = "jetbrains"
    var txtDelay = 100
    var customRev = 0
    var textRev = 0
    if (raw && String(raw).trim() !== "") {
      try {
        var obj = JSON.parse(raw)
        if (obj && obj.enabled === false) on = false
        if (obj && obj.source === "custom") nextPreset = "custom"
        else if (obj && obj.source === "static") nextPreset = "static"
        else if (obj && obj.source === "spin") nextPreset = "spin"
        else if (obj && obj.source === "waves") nextPreset = "waves"
        else if (obj && obj.source === "text") nextPreset = "text"
        if (obj && obj.label) label = String(obj.label)
        if (obj && obj.invert === true) inv = true
        if (obj && obj.threshold !== undefined) thr = Math.max(5, Math.min(95, Math.round(Number(obj.threshold))))
        if (obj && obj.delayMs !== undefined) delay = Math.max(50, Math.min(500, Math.round(Number(obj.delayMs))))
        if (obj && obj.sourceFile) srcFile = String(obj.sourceFile)
        if (obj && obj.text) txt = String(obj.text)
        if (obj && obj.textStyle && textStyleOrder.indexOf(String(obj.textStyle)) !== -1) txtStyle = String(obj.textStyle)
        if (obj && obj.textFont && textFontOrder.indexOf(String(obj.textFont)) !== -1) txtFont = String(obj.textFont)
        if (obj && obj.textDelayMs !== undefined) txtDelay = Math.max(50, Math.min(500, Math.round(Number(obj.textDelayMs))))
        if (obj && obj.customPreviewRev !== undefined) customRev = Math.max(0, Math.round(Number(obj.customPreviewRev)))
        if (obj && obj.textPreviewRev !== undefined) textRev = Math.max(0, Math.round(Number(obj.textPreviewRev)))
      } catch (e) {}
    }
    invert = inv
    threshold = thr
    delayMs = delay
    sourceFile = srcFile
    customText = txt
    textStyle = txtStyle
    textFont = txtFont
    textDelayMs = txtDelay
    customPreviewRev = customRev
    textPreviewRev = textRev
    preset = nextPreset
    if (nextPreset === "spin") sourceLabel = "Spin"
    else if (nextPreset === "waves") sourceLabel = "Waves"
    else if (nextPreset === "static") sourceLabel = "Static"
    else if (nextPreset === "custom") sourceLabel = label || "Custom"
    else if (nextPreset === "text") sourceLabel = txt || "Text"
    else sourceLabel = "Omarchy"
    settingsLoaded = true
    setEnabled(on)
  }

  function scheduleSave() {
    if (!settingsLoaded) return
    saveTimer.restart()
  }

  function flushSettings() {
    settingsFile.setText(JSON.stringify({
      enabled: enabled,
      source: preset,
      label: sourceLabel,
      invert: invert,
      threshold: threshold,
      delayMs: delayMs,
      sourceFile: sourceFile,
      text: customText,
      textStyle: textStyle,
      textFont: textFont,
      textDelayMs: textDelayMs,
      customPreviewRev: customPreviewRev,
      textPreviewRev: textPreviewRev
    }, null, 2) + "\n")
  }

  onPluginDirChanged: if (settingsLoaded && enabled) root.startWatch()
  Component.onCompleted: settingsFile.reload()

  FileView {
    id: settingsFile
    path: root.settingsPath
    watchChanges: false
    atomicWrites: true
    printErrors: false
    onLoaded: root.loadSettings(text())
    onLoadFailed: root.loadSettings("")
  }

  FileView {
    path: root.customFrames
    watchChanges: true
    printErrors: false
    onLoaded: root.hasCustom = true
    onLoadFailed: root.hasCustom = false
  }

  FileView {
    path: root.textFrames
    watchChanges: true
    printErrors: false
    onLoaded: root.hasText = true
    onLoadFailed: root.hasText = false
  }

  Timer {
    id: saveTimer
    interval: 200
    repeat: false
    onTriggered: root.flushSettings()
  }

  Timer {
    id: thresholdTimer
    interval: 350
    repeat: false
    onTriggered: if (root.isCustom) root.reimportSaved()
  }

  Process {
    id: watchProcess
    running: false
    stdout: SplitParser {
      onRead: function(value) { root.handleLine(value) }
    }
    stderr: SplitParser {
      onRead: function(value) {
        if (value && root.enabled) root.lastError = String(value).slice(0, 200)
      }
    }
    onExited: {
      if (!root.stopping && root.enabled)
        restartTimer.restart()
    }
  }

  Process {
    id: restProcess
    running: false
  }

  // pickProcess and importProcess only ever produce a single short line
  // (a filesystem path, or apply.py's one-line status JSON, both naturally
  // bounded by filesystem name limits), but StdioCollector buffers whatever
  // it is given before any check runs, so a helper misbehaving out of
  // process still shouldn't get its output trusted or acted on unbounded.
  readonly property int maxHelperOutput: 8192

  function boundedCollectorText(collector) {
    var text = collector && collector.text ? String(collector.text) : ""
    return text.length > maxHelperOutput ? "" : text
  }

  Process {
    id: pickProcess
    running: false
    stdout: StdioCollector { id: pickOut; waitForEnd: true }
    onExited: function(code) {
      root.pickBusy = false
      var path = root.boundedCollectorText(pickOut).trim()
      if (code === 0 && path !== "") root.importImage(path)
    }
  }

  Process {
    id: importProcess
    running: false
    stdout: StdioCollector { id: importOut; waitForEnd: true }
    stderr: StdioCollector { id: importErr; waitForEnd: true }
    onExited: function(code) {
      root.importBusy = false
      if (code !== 0) {
        root.keepDelayOnImport = false
        var err = root.boundedCollectorText(importErr).trim()
        root.lastError = (err !== "" ? err : (root.pendingKind === "text" ? "Could not render text" : "Could not import image")).slice(0, 200)
        return
      }
      if (root.pendingKind === "text") {
        var textMsg = null
        try { textMsg = JSON.parse(root.boundedCollectorText(importOut).trim()) } catch (e) {}
        if (textMsg && textMsg.label !== undefined) root.customText = String(textMsg.label)
        if (textMsg && textMsg.style) root.textStyle = String(textMsg.style)
        if (textMsg && textMsg.font) root.textFont = String(textMsg.font)
        if (textMsg && textMsg.delay_ms)
          root.textDelayMs = Math.max(50, Math.min(500, Math.round(Number(textMsg.delay_ms))))
        root.hasText = true
        root.textPreviewRev = root.pendingPreviewSuffix
        root.lastError = ""
        root.setPreset("text")
        return
      }
      var label = root.sourceLabel !== "" ? root.sourceLabel : "Custom"
      var srcFile = root.sourceFile
      var delay = root.delayMs
      try {
        var msg = JSON.parse(root.boundedCollectorText(importOut).trim())
        if (msg && msg.label && !root.keepDelayOnImport) label = String(msg.label)
        if (msg && msg.source) srcFile = String(msg.source)
        if (msg && msg.delay_ms && !root.keepDelayOnImport)
          delay = Math.max(50, Math.min(500, Math.round(Number(msg.delay_ms))))
      } catch (e) {}
      root.keepDelayOnImport = false
      root.preset = "custom"
      root.hasCustom = true
      root.sourceLabel = label
      root.sourceFile = srcFile
      root.delayMs = delay
      root.customPreviewRev = root.pendingPreviewSuffix
      root.lastError = ""
      if (root.settingsLoaded) root.scheduleSave()
      root.setEnabled(true)
    }
  }

  Process {
    id: udevProcess
    onExited: function(code) {
      root.udevBusy = false
      if (code === 0) {
        root.lastError = ""
      } else {
        root.lastError = "Access request cancelled"
      }
    }
  }

  Timer {
    id: restartTimer
    interval: 5000
    repeat: false
    onTriggered: root.startWatch()
  }

  IpcHandler {
    target: "io.github.auxxed.steelseries-oled"
    function udev(): void { root.installUdev() }
    function status(): string { return root.statusLabel }
    function power(): void { root.toggleEnabled() }
  }

  Component.onDestruction: {
    stopping = true
    restartTimer.stop()
    saveTimer.stop()
    watchProcess.running = false
    if (pluginDir !== "" && !restProcess.running) {
      restProcess.command = restCommand()
      restProcess.running = true
    }
  }
}

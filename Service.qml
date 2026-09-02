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
  property bool isCustom: false
  property bool hasCustom: false
  property bool invert: false
  property int threshold: 50
  property int delayMs: 100
  property string sourceLabel: "Omarchy"
  property string sourceFile: ""
  property int previewRev: 0
  property bool importBusy: false
  property bool pickBusy: false
  property bool keepDelayOnImport: false

  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string stateHome: Quickshell.env("XDG_STATE_HOME") || (home + "/.local/state")
  readonly property string settingsPath: stateHome + "/omarchy/steelseries-oled.json"
  readonly property string customDir: stateHome + "/omarchy/steelseries-oled"
  readonly property string customFrames: customDir + "/custom.frames"
  readonly property string customRest: customDir + "/custom.bin"
  readonly property string customPreview: customDir + "/preview.png"
  readonly property string customSource: customDir + "/" + (sourceFile !== "" ? sourceFile : "source.gif")

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
  readonly property string defaultPreview: pluginDir + "/assets/omarchy-oled-128x40.png"
  readonly property string previewUrl: {
    var path = isCustom ? customPreview : defaultPreview
    return "file://" + path + "#" + previewRev
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
    }
    return cmd
  }

  function restCommand() {
    var cmd = ["python3", "-u", helper, "--once"]
    if (invert) cmd.push("--invert")
    if (isCustom) cmd.push("--rest", customRest)
    return cmd
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
    lastError = ""
    importBusy = true
    importProcess.command = [
      "python3", "-u", helper, "--import", path,
      "--out-dir", customDir,
      "--threshold", String(threshold)
    ]
    importProcess.running = true
  }

  function reimportSaved() {
    if (!hasCustom && sourceFile === "") return
    keepDelayOnImport = true
    importImage(customSource)
  }

  function resetDefault() {
    isCustom = false
    sourceLabel = "Omarchy"
    previewRev = previewRev + 1
    lastError = ""
    if (settingsLoaded) scheduleSave()
    if (enabled) startWatch()
  }

  function useCustom() {
    if (!hasCustom) return
    isCustom = true
    if (sourceLabel === "" || sourceLabel === "Omarchy") sourceLabel = "Custom"
    previewRev = previewRev + 1
    lastError = ""
    if (settingsLoaded) scheduleSave()
    if (enabled) startWatch()
    else setEnabled(true)
  }

  function setInvert(on) {
    on = !!on
    if (invert === on) return
    invert = on
    if (settingsLoaded) scheduleSave()
    if (enabled) startWatch()
    else if (pluginDir !== "") {
      restProcess.command = restCommand()
      restProcess.running = true
    }
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
    var custom = false
    var label = "Omarchy"
    var inv = false
    var thr = 50
    var delay = 100
    var srcFile = ""
    if (raw && String(raw).trim() !== "") {
      try {
        var obj = JSON.parse(raw)
        if (obj && obj.enabled === false) on = false
        if (obj && obj.source === "custom") custom = true
        if (obj && obj.label) label = String(obj.label)
        if (obj && obj.invert === true) inv = true
        if (obj && obj.threshold !== undefined) thr = Math.max(5, Math.min(95, Math.round(Number(obj.threshold))))
        if (obj && obj.delayMs !== undefined) delay = Math.max(50, Math.min(500, Math.round(Number(obj.delayMs))))
        if (obj && obj.sourceFile) srcFile = String(obj.sourceFile)
      } catch (e) {}
    }
    invert = inv
    threshold = thr
    delayMs = delay
    sourceFile = srcFile
    isCustom = custom
    sourceLabel = custom ? label : "Omarchy"
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
      source: isCustom ? "custom" : "default",
      label: sourceLabel,
      invert: invert,
      threshold: threshold,
      delayMs: delayMs,
      sourceFile: sourceFile
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

  Process {
    id: pickProcess
    running: false
    stdout: StdioCollector { id: pickOut; waitForEnd: true }
    onExited: function(code) {
      root.pickBusy = false
      var path = pickOut.text ? String(pickOut.text).trim() : ""
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
        var err = importErr.text ? String(importErr.text).trim() : ""
        root.lastError = (err !== "" ? err : "Could not import image").slice(0, 200)
        return
      }
      var label = root.sourceLabel !== "" ? root.sourceLabel : "Custom"
      var srcFile = root.sourceFile
      var delay = root.delayMs
      try {
        var msg = JSON.parse(String(importOut.text || "").trim())
        if (msg && msg.label && !root.keepDelayOnImport) label = String(msg.label)
        if (msg && msg.source) srcFile = String(msg.source)
        if (msg && msg.delay_ms && !root.keepDelayOnImport)
          delay = Math.max(50, Math.min(500, Math.round(Number(msg.delay_ms))))
      } catch (e) {}
      root.keepDelayOnImport = false
      root.isCustom = true
      root.hasCustom = true
      root.sourceLabel = label
      root.sourceFile = srcFile
      root.delayMs = delay
      root.previewRev = root.previewRev + 1
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
  }
}

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

  readonly property bool present: state === "looping" || state === "denied"
  readonly property bool looping: state === "looping"
  readonly property bool needsUdev: present && !permission
  readonly property string statusLabel: {
    if (needsUdev) return "Needs access"
    if (looping) return "Looping"
    if (state === "disconnected") return "No keyboard"
    if (lastError !== "") return lastError
    return "Starting"
  }

  function startWatch() {
    if (stopping || pluginDir === "")
      return
    if (watchProcess.running)
      watchProcess.running = false
    watchProcess.command = ["python3", "-u", helper, "--watch"]
    watchProcess.running = true
  }

  function handleLine(line) {
    if (!line) return
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
    lastError = "Asking for permission to install the hidraw udev rule…"
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

  onPluginDirChanged: root.startWatch()
  Component.onCompleted: root.startWatch()

  Process {
    id: watchProcess
    running: false
    stdout: SplitParser {
      onRead: function(value) { root.handleLine(value) }
    }
    stderr: SplitParser {
      onRead: function(value) {
        if (value) root.lastError = String(value).slice(0, 200)
      }
    }
    onExited: {
      if (!root.stopping)
        restartTimer.restart()
    }
  }

  Process {
    id: udevProcess
    onExited: function(code) {
      if (code === 0) {
        root.lastError = ""
      } else {
        root.lastError = "Udev install cancelled or failed"
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
  }

  Component.onDestruction: {
    stopping = true
    restartTimer.stop()
    watchProcess.running = false
  }
}

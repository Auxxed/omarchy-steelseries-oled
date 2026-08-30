import QtQuick
import Quickshell
import Quickshell.Io

// Headless service: keep the Omarchy wordmark on a SteelSeries Apex OLED.
// apply.py watches hidraw and writes the image when the keyboard appears.
Item {
  id: root

  property var manifest: null
  property var shell: null
  property bool stopping: false

  readonly property string pluginDir: manifest && manifest.__sourceDir ? String(manifest.__sourceDir) : ""
  readonly property string helper: pluginDir + "/apply.py"

  function startWatch() {
    if (stopping || pluginDir === "")
      return
    if (watchProcess.running)
      watchProcess.running = false
    watchProcess.command = ["python3", "-u", helper, "--watch"]
    watchProcess.running = true
  }

  onPluginDirChanged: root.startWatch()
  Component.onCompleted: root.startWatch()

  Process {
    id: watchProcess
    running: false
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("steelseries-oled", text.trim())
    }
    onExited: {
      if (!root.stopping)
        restartTimer.restart()
    }
  }

  Timer {
    id: restartTimer
    interval: 5000
    repeat: false
    onTriggered: root.startWatch()
  }

  Component.onDestruction: {
    stopping = true
    restartTimer.stop()
    watchProcess.running = false
  }
}

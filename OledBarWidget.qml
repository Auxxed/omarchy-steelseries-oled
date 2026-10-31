import QtQuick
import Quickshell.Io
import qs.Ui

BarWidget {
  id: root
  moduleName: "io.github.auxxed.steelseries-oled"

  readonly property var oled: bar && bar.shell
    ? bar.shell.serviceFor(moduleName)
    : null
  readonly property bool ready: !!oled

  readonly property bool opened: panelItem ? panelItem.opened === true : false
  readonly property bool popoutSwitchClosing: panelItem ? panelItem.popoutSwitchClosing === true : false

  property var panelItem: null

  function open() { if (panelItem) panelItem.open() }
  function close() { if (panelItem) panelItem.close() }
  function toggle() { if (panelItem) panelItem.toggle() }
  function closeForPopoutSwitch() { if (panelItem) panelItem.closeForPopoutSwitch() }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    panelItem = target
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("OledPanel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  // One bar widget per monitor, one IPC target: the first widget answers,
  // and opens the panel on the focused monitor through the shell.
  property bool ipcOwner: false

  function viaShell(action) {
    var sh = bar ? bar.shell : null
    if (!sh || typeof sh[action] !== "function") return false
    if (action === "hide") sh.hide(moduleName)
    else sh[action](moduleName, "{}")
    return true
  }

  Timer {
    interval: 1500
    running: true
    onTriggered: {
      var items = root.bar && typeof root.bar.moduleWidgets === "function" ? root.bar.moduleWidgets(root.moduleName) : null
      root.ipcOwner = !items || !items.length || items[0] === root
    }
  }

  IpcHandler {
    target: "io.github.auxxed.steelseries-oled.panel"
    enabled: root.ipcOwner

    function open(): void { if (!root.viaShell("summon")) root.open() }
    function close(): void { if (!root.viaShell("hide")) root.close() }
    function show(): void { open() }
    function hide(): void { close() }
    function toggle(): void { if (!root.viaShell("toggle")) root.toggle() }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "\uF11C"
    active: false
    useActiveColor: false
    dimmed: !(root.ready && root.oled.enabled)
    tooltipText: {
      if (!root.ready) return "OLED"
      if (!root.oled.enabled) return "OLED off"
      if (root.oled.sleeping) return "OLED asleep"
      return "OLED: " + root.oled.showingLabel
    }
    onPressed: function(b) {
      if (b === Qt.LeftButton) root.toggle()
      else if (b === Qt.RightButton && root.ready) root.oled.toggleEnabled()
    }
  }
}

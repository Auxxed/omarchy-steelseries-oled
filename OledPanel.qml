import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "io.github.auxxed.steelseries-oled"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root
  readonly property var oled: hostWidget && hostWidget.oled ? hostWidget.oled : null
  readonly property bool ready: !!oled
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool canOpen: bar !== null && anchorItem !== null

  function open() {
    if (!canOpen) return
    root.controller.show()
  }

  function close() {
    root.controller.hide()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(320))
    contentHeight: panel.fittedContentHeight(content.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if ((t === "u" || t === "U") && root.ready) root.oled.installUdev()
      }

      Column {
        id: content
        width: parent.width
        spacing: Style.space(12)

        PanelHero {
          width: parent.width
          title: "SteelSeries OLED"
          meta: root.ready ? root.oled.statusLabel : "Starting…"
          detail: root.ready && root.oled.devicePath !== ""
            ? root.oled.devicePath
            : "Apex 7 / Pro / 5"
          foreground: root.foreground
          fontFamily: root.fontFamily
        }

        Text {
          width: parent.width
          wrapMode: Text.WordWrap
          text: "Streams the Omarchy wordmark GIF to the keyboard OLED while this plugin is enabled."
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }

        Text {
          visible: root.ready && (root.oled.needsUdev || root.oled.lastError !== "")
          width: parent.width
          wrapMode: Text.WordWrap
          text: root.ready && root.oled.needsUdev
            ? "The keyboard is there, but this session cannot write hidraw. Install the udev rule once."
            : (root.ready ? root.oled.lastError : "")
          color: Color.urgent
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        Button {
          visible: root.ready && root.oled.needsUdev
          text: "Install udev rule"
          foreground: root.foreground
          onClicked: root.oled.installUdev()
        }

        Text {
          width: parent.width
          wrapMode: Text.WordWrap
          text: "The Apex OLED has no onboard animation storage, so onboard menus lose the fight while the service is running. python3 apply.py --once leaves a still wordmark."
          color: Qt.darker(root.foreground, 1.55)
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }
      }
    }
  }
}

import QtQuick
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
  readonly property bool on: ready && oled.enabled
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
    contentHeight: panel.fittedContentHeight(content.implicitHeight, Style.space(520))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onActivateRequested: if (root.ready) root.oled.toggleEnabled()
      onTextKey: function(t) {
        if (t === " " && root.ready) root.oled.toggleEnabled()
        else if ((t === "i" || t === "I") && root.ready) root.oled.toggleInvert()
        else if ((t === "u" || t === "U") && root.ready && root.oled.needsUdev)
          root.oled.installUdev()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: content.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height

        Column {
          id: content
          width: panelFlick.width
          spacing: Style.space(10)

          PanelHero {
            width: parent.width
            title: "OLED"
            meta: root.ready ? root.oled.statusLabel : "…"
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconOpacity: root.on ? 1.0 : 0.4
            iconComponent: Component {
              Text {
                textFormat: Text.PlainText
                text: "\uF11C"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }
          }

          Toggle {
            width: parent.width
            label: "Display"
            checked: root.on
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: if (root.ready) root.oled.toggleEnabled()
          }

          Toggle {
            width: parent.width
            label: "Invert"
            checked: root.ready && root.oled.invert
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: if (root.ready) root.oled.toggleInvert()
          }

          Column {
            width: parent.width
            spacing: Style.space(6)

            Item {
              id: previewBox
              width: parent.width
              height: 40
              property string url: root.ready ? root.oled.previewUrl : ""
              property string token: url + "|" + (root.ready ? root.oled.previewRev : 0)

              Loader {
                id: previewLoader
                anchors.fill: parent
                sourceComponent: previewComp
              }

              Component {
                id: previewComp
                AnimatedImage {
                  anchors.fill: parent
                  fillMode: Image.PreserveAspectFit
                  asynchronous: false
                  cache: true
                  smooth: false
                  playing: true
                  source: previewBox.url
                  opacity: root.on ? 1 : 0.4
                  layer.enabled: root.ready && root.oled.invert
                  layer.smooth: false
                  layer.effect: ShaderEffect {
                    fragmentShader: Qt.resolvedUrl("invert.frag.qsb")
                  }
                  onStatusChanged: if (status === AnimatedImage.Ready) playing = true
                }
              }

              function reloadPreview() {
                previewLoader.sourceComponent = null
                Qt.callLater(function() { previewLoader.sourceComponent = previewComp })
              }

              onTokenChanged: reloadPreview()
            }

            Text {
              width: parent.width
              horizontalAlignment: Text.AlignHCenter
              elide: Text.ElideMiddle
              text: root.ready ? root.oled.sourceLabel : ""
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }
          }

          Text {
            text: "Contrast  " + (root.ready ? root.oled.threshold : 50) + "%"
            color: Qt.darker(root.foreground, 1.45)
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            font.letterSpacing: 1.2
            opacity: root.ready && root.oled.isCustom ? 1 : 0.4
          }

          PanelSlider {
            width: parent.width
            bar: root.bar
            enabled: root.ready && root.oled.isCustom
            opacity: enabled ? 1 : 0.4
            value: root.ready ? root.oled.threshold : 50
            minimum: 10
            maximum: 90
            step: 5
            integer: true
            onReleased: function(v) { if (root.ready && root.oled.isCustom) root.oled.setThreshold(v) }
          }

          Text {
            text: "Speed  " + (root.ready ? root.oled.delayMs : 100) + " ms"
            color: Qt.darker(root.foreground, 1.45)
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            font.letterSpacing: 1.2
          }

          PanelSlider {
            width: parent.width
            bar: root.bar
            value: root.ready ? root.oled.delayMs : 100
            minimum: 50
            maximum: 400
            step: 10
            integer: true
            onReleased: function(v) { if (root.ready) root.oled.setDelayMs(v) }
          }

          Button {
            width: parent.width
            text: root.ready && (root.oled.pickBusy || root.oled.importBusy)
              ? "Importing…"
              : "Choose image"
            foreground: root.foreground
            onClicked: {
              if (!root.ready || root.oled.pickBusy || root.oled.importBusy) return
              root.close()
              root.oled.pickImage()
            }
          }

          Button {
            width: parent.width
            text: "Cycle"
            foreground: root.foreground
            onClicked: if (root.ready) root.oled.cycleBundled()
          }

          Button {
            visible: root.ready && root.oled.hasCustom
            enabled: root.ready && root.oled.preset !== "custom"
            opacity: enabled ? 1 : 0.4
            width: parent.width
            text: "Use last image"
            foreground: root.foreground
            onClicked: if (root.ready && root.oled.preset !== "custom") root.oled.useCustom()
          }

          Button {
            visible: root.ready && root.oled.needsUdev
            width: parent.width
            text: (root.ready && root.oled.udevBusy) ? "Waiting…" : "Allow access"
            foreground: root.foreground
            onClicked: if (root.ready) root.oled.installUdev()
          }

          Text {
            visible: root.ready && root.oled.lastError !== "" && !root.oled.needsUdev && !root.oled.udevBusy
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.ready ? root.oled.lastError : ""
            color: Color.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }
        }
      }
    }
  }
}

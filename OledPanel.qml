import QtQuick
import QtQuick.Controls as QQC
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

              AnimatedImage {
                anchors.fill: parent
                fillMode: Image.PreserveAspectFit
                asynchronous: false
                cache: true
                smooth: false
                playing: true
                // Every custom/text render writes a uniquely-numbered preview
                // file (apply.py's preview_paths()) instead of overwriting one
                // fixed name, so this URL is only ever equal to a previous one
                // when the content is actually the same — no cache-busting
                // trick needed, and no stale frame to worry about.
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

            Text {
              width: parent.width
              textFormat: Text.PlainText
              horizontalAlignment: Text.AlignHCenter
              elide: Text.ElideMiddle
              text: root.ready ? root.oled.sourceLabel : ""
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }
          }

          Text {
            textFormat: Text.PlainText
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
            textFormat: Text.PlainText
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

          Row {
            width: parent.width
            spacing: Style.space(6)

            Button {
              width: parent.width - webGifButton.width - parent.spacing
              text: "Omarchy Logo"
              foreground: root.foreground
              onClicked: if (root.ready) root.oled.cycleBundled()
            }

            Button {
              id: webGifButton
              text: root.ready && root.oled.importBusy
                ? "…"
                : (root.ready && root.oled.webGifIndex >= 0 ? String(root.oled.webGifIndex + 1) : "#")
              tooltipText: root.ready && root.oled.webGifIndex >= 0
                ? "#" + (root.oled.webGifIndex + 1) + " " + root.oled.webGifs[root.oled.webGifIndex].name + " (nlog.us) — click for next, right-click for all"
                : "Grab the next OLED gif from nlog.us (right-click for all)"
              foreground: root.foreground
              opacity: (root.ready && !root.oled.importBusy && !root.oled.pickBusy) ? 1 : 0.4
              onClicked: if (root.ready) root.oled.fetchWebGif()
              onRightClicked: if (root.ready) webGifMenu.opened ? webGifMenu.close() : webGifMenu.open()

              function gifOptions(query) {
                var out = []
                if (!root.ready) return out
                var q = query.toLowerCase()
                var all = root.oled.webGifs
                for (var i = 0; i < all.length; i++) {
                  if (q === "" || all[i].name.toLowerCase().indexOf(q) !== -1)
                    out.push({ idx: i, name: all[i].name })
                }
                return out
              }

              QQC.Popup {
                id: webGifMenu
                x: webGifButton.width - width
                y: webGifButton.height + Style.space(4)
                width: Style.space(200)
                height: Style.space(260)
                padding: Style.spacing.hairline
                focus: true

                background: BorderSurface {
                  color: Color.popups.background
                  borderSpec: Border.localOrSurfaceSpec("popups", "border", Color.popups.border, Color.popups.border, Style.normalBorderWidth)
                  radius: Style.cornerRadius
                }

                onOpened: {
                  gifFilter.text = ""
                  Qt.callLater(function() { gifFilter.forceActiveFocus() })
                }

                contentItem: Column {
                  spacing: Style.spacing.xxs

                  TextField {
                    id: gifFilter
                    width: parent.width
                    placeholderText: "Search…"
                    foreground: root.foreground
                    font.family: root.fontFamily
                    Keys.onEscapePressed: webGifMenu.close()
                  }

                  Text {
                    textFormat: Text.PlainText
                    visible: gifList.count === 0
                    text: "No matches"
                    color: Qt.darker(root.foreground, 1.6)
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                  }

                  ListView {
                    id: gifList
                    width: parent.width
                    height: webGifMenu.height - gifFilter.height - Style.spacing.xxs - webGifMenu.topPadding - webGifMenu.bottomPadding
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: webGifButton.gifOptions(gifFilter.text)

                    delegate: Rectangle {
                      required property var modelData
                      width: gifList.width
                      height: Style.space(26)
                      color: rowHover.hovered ? Style.hoverFillFor(root.foreground, Color.accent) : "transparent"

                      Text {
                        textFormat: Text.PlainText
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: Style.spacing.controlPaddingX
                        anchors.rightMargin: Style.spacing.controlPaddingX
                        text: "#" + (modelData.idx + 1) + "  " + modelData.name
                        color: root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                        elide: Text.ElideRight
                      }

                      HoverHandler { id: rowHover }
                      MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                          root.oled.jumpToWebGif(modelData.idx)
                          webGifMenu.close()
                        }
                      }
                    }
                  }
                }
              }
            }
          }

          Row {
            width: parent.width
            spacing: Style.space(6)

            TextField {
              id: textInput
              width: parent.width - textFontButton.width - textStyleButton.width - parent.spacing * 2
              placeholderText: "Type your own…"
              text: root.ready ? root.oled.customText : ""
              onAccepted: if (root.ready && text.trim() !== "") root.oled.setCustomText(text)
            }

            Button {
              id: textFontButton
              text: {
                if (root.ready && root.oled.importBusy) return "…"
                if (!root.ready || !root.oled.hasText) return "Font"
                return root.oled.textFontLabels[root.oled.textFont] || root.oled.textFont
              }
              tooltipText: root.ready && root.oled.hasText
                ? "Font: " + (root.oled.textFontLabels[root.oled.textFont] || root.oled.textFont) + " — click to cycle, right-click to pick"
                : "Font used to render typed text"
              foreground: root.foreground
              opacity: (root.ready && !root.oled.importBusy && !root.oled.pickBusy) ? 1 : 0.4
              onClicked: {
                if (!root.ready || !root.oled.hasText) return
                root.oled.cycleTextFont()
              }
              onRightClicked: {
                if (!root.ready || !root.oled.hasText) return
                textFontMenu.opened ? textFontMenu.close() : textFontMenu.open()
              }

              QQC.Popup {
                id: textFontMenu
                x: textFontButton.width - width
                y: textFontButton.height + Style.space(4)
                width: Style.space(140)
                height: fontList.implicitHeight + topPadding + bottomPadding
                padding: Style.spacing.hairline
                focus: true

                background: BorderSurface {
                  color: Color.popups.background
                  borderSpec: Border.localOrSurfaceSpec("popups", "border", Color.popups.border, Color.popups.border, Style.normalBorderWidth)
                  radius: Style.cornerRadius
                }

                contentItem: Column {
                  id: fontList
                  spacing: 0

                  Repeater {
                    model: root.ready ? root.oled.textFontOrder : []
                    delegate: Rectangle {
                      required property var modelData
                      width: fontList.width
                      height: Style.space(26)
                      color: fontHover.hovered ? Style.hoverFillFor(root.foreground, Color.accent) : "transparent"

                      Text {
                        textFormat: Text.PlainText
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: Style.spacing.controlPaddingX
                        anchors.rightMargin: Style.spacing.controlPaddingX
                        text: root.oled.textFontLabels[modelData] || modelData
                        font.bold: root.ready && modelData === root.oled.textFont
                        color: root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                      }

                      HoverHandler { id: fontHover }
                      MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                          root.oled.setTextFont(modelData)
                          textFontMenu.close()
                        }
                      }
                    }
                  }
                }
              }
            }

            Button {
              id: textStyleButton
              text: {
                if (root.ready && root.oled.importBusy) return "…"
                if (!root.ready || !root.oled.hasText) return "Set"
                var s = root.oled.textStyle
                return s.charAt(0).toUpperCase() + s.slice(1)
              }
              tooltipText: root.ready && root.oled.hasText
                ? "Style: " + root.oled.textStyle + " — click to cycle, right-click to pick"
                : "Render the typed text (128x40, 1-bit)"
              foreground: root.foreground
              opacity: (root.ready && !root.oled.importBusy && !root.oled.pickBusy) ? 1 : 0.4
              onClicked: {
                if (!root.ready) return
                var typed = textInput.text.trim()
                if (!root.oled.hasText || typed !== root.oled.customText) {
                  if (typed !== "") root.oled.setCustomText(typed)
                } else {
                  root.oled.cycleTextStyle()
                }
              }
              onRightClicked: {
                if (!root.ready || !root.oled.hasText) return
                textStyleMenu.opened ? textStyleMenu.close() : textStyleMenu.open()
              }

              QQC.Popup {
                id: textStyleMenu
                x: textStyleButton.width - width
                y: textStyleButton.height + Style.space(4)
                width: Style.space(140)
                height: styleList.implicitHeight + topPadding + bottomPadding
                padding: Style.spacing.hairline
                focus: true

                background: BorderSurface {
                  color: Color.popups.background
                  borderSpec: Border.localOrSurfaceSpec("popups", "border", Color.popups.border, Color.popups.border, Style.normalBorderWidth)
                  radius: Style.cornerRadius
                }

                contentItem: Column {
                  id: styleList
                  spacing: 0

                  Repeater {
                    model: root.ready ? root.oled.textStyleOrder : []
                    delegate: Rectangle {
                      required property var modelData
                      width: styleList.width
                      height: Style.space(26)
                      color: styleHover.hovered ? Style.hoverFillFor(root.foreground, Color.accent) : "transparent"

                      Text {
                        textFormat: Text.PlainText
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: Style.spacing.controlPaddingX
                        anchors.rightMargin: Style.spacing.controlPaddingX
                        text: modelData.charAt(0).toUpperCase() + modelData.slice(1)
                        font.bold: root.ready && modelData === root.oled.textStyle
                        color: root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                      }

                      HoverHandler { id: styleHover }
                      MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                          root.oled.setTextStyle(modelData)
                          textStyleMenu.close()
                        }
                      }
                    }
                  }
                }
              }
            }
          }

          Button {
            visible: root.ready && root.oled.hasText
            enabled: root.ready && root.oled.preset !== "text"
            opacity: enabled ? 1 : 0.4
            width: parent.width
            text: "Use text"
            foreground: root.foreground
            onClicked: if (root.ready && root.oled.preset !== "text") root.oled.useText()
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
            textFormat: Text.PlainText
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

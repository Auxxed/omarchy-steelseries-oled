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
  readonly property bool live: ready && oled.looping
  readonly property bool asleep: on && oled.sleeping
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.45)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool canOpen: bar !== null && anchorItem !== null
  readonly property bool busy: ready && (oled.importBusy || oled.pickBusy)

  // Which source tab is showing. Follows whatever is playing each time the
  // panel opens; switching tabs on its own never changes the OLED.
  property string tab: "logo"
  readonly property var tabs: [
    { value: "logo", label: "Logo" },
    { value: "effects", label: "Effects" },
    { value: "images", label: "Images" },
    { value: "text", label: "Text" }
  ]

  function tabForPreset() {
    if (!ready) return "logo"
    if (oled.isScreensaver) return "effects"
    if (oled.isCustom) return "images"
    if (oled.isText) return "text"
    return "logo"
  }

  onOpenedChanged: if (opened) tab = tabForPreset()

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
    contentWidth: panel.fittedContentWidth(Style.space(440))
    contentHeight: panel.fittedContentHeight(content.implicitHeight, Style.space(780))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onActivateRequested: if (root.ready) root.oled.toggleEnabled()
      onTextKey: function(t) {
        if (t === " " && root.ready) root.oled.toggleEnabled()
        else if ((t === "i" || t === "I") && root.ready) root.oled.toggleInvert()
        else if ((t === "r" || t === "R") && root.ready) root.oled.surpriseMe(root.tab)
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
          spacing: Style.space(12)

          // ── Header ────────────────────────────────────────────────
          PanelHero {
            width: parent.width
            title: "SteelSeries OLED"
            meta: root.ready ? root.oled.statusLabel : "…"
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconOpacity: root.on ? 1.0 : 0.4
            iconComponent: Component {
              Text {
                textFormat: Text.PlainText
                text: ""
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }
            trailingControl: Component {
              ToggleSwitch {
                checked: root.on
                foreground: root.foreground
                onToggled: if (root.ready) root.oled.toggleEnabled()
              }
            }
          }

          // ── The stage: a pixel-exact stand-in for the keyboard OLED ──
          Item {
            id: stage
            width: parent.width
            height: bezel.height + Style.space(6)

            // Largest whole-number scale that fits, so every OLED pixel
            // lands on a crisp block and the dot grid lines up.
            readonly property int px: Math.max(1, Math.floor((width - Style.space(24)) / 128))

            Rectangle {
              id: glow
              anchors.fill: bezel
              anchors.margins: -Style.space(3)
              radius: bezel.radius + Style.space(3)
              color: "transparent"
              border.width: Style.space(2)
              border.color: Color.accent
              opacity: 0
              visible: root.live

              SequentialAnimation on opacity {
                running: root.live && root.opened
                loops: Animation.Infinite
                NumberAnimation { from: 0.08; to: 0.45; duration: 1600; easing.type: Easing.InOutSine }
                NumberAnimation { from: 0.45; to: 0.08; duration: 1600; easing.type: Easing.InOutSine }
              }
            }

            Rectangle {
              id: bezel
              anchors.horizontalCenter: parent.horizontalCenter
              anchors.bottom: parent.bottom
              width: screen.width + Style.space(20)
              height: screen.height + Style.space(20)
              radius: Math.max(Style.cornerRadius, Style.space(6))
              color: "#050505"
              border.width: 1
              border.color: Util.alpha(root.foreground, 0.18)

              Item {
                id: screen
                anchors.centerIn: parent
                width: 128 * stage.px
                height: 40 * stage.px
                clip: true

                AnimatedImage {
                  anchors.fill: parent
                  fillMode: Image.Stretch
                  asynchronous: false
                  cache: true
                  smooth: false
                  playing: true
                  // Every custom/text render writes a uniquely-numbered preview
                  // file (apply.py's preview_paths()) instead of overwriting one
                  // fixed name, so this URL is only ever equal to a previous one
                  // when the content is actually the same — no cache-busting
                  // trick needed, and no stale frame to worry about.
                  source: root.ready ? root.oled.previewUrl : ""
                  opacity: root.on && !root.asleep ? 1 : 0.25
                  layer.enabled: root.ready && root.oled.invert
                  layer.smooth: false
                  layer.effect: ShaderEffect {
                    fragmentShader: Qt.resolvedUrl("invert.frag.qsb")
                  }
                  onStatusChanged: if (status === AnimatedImage.Ready) playing = true

                  Behavior on opacity { NumberAnimation { duration: 220 } }
                }

                // Dot-matrix grid: a hairline between every OLED pixel.
                Canvas {
                  anchors.fill: parent
                  visible: stage.px >= 3
                  onWidthChanged: requestPaint()
                  onHeightChanged: requestPaint()
                  onPaint: {
                    var ctx = getContext("2d")
                    ctx.reset()
                    ctx.fillStyle = "rgba(0, 0, 0, 0.45)"
                    for (var x = stage.px; x < width; x += stage.px) ctx.fillRect(x - 1, 0, 1, height)
                    for (var y = stage.px; y < height; y += stage.px) ctx.fillRect(0, y - 1, width, 1)
                  }
                }

                Text {
                  anchors.centerIn: parent
                  visible: root.ready && (!root.on || root.asleep)
                  textFormat: Text.PlainText
                  text: root.asleep ? "ASLEEP" : "DISPLAY OFF"
                  color: Util.alpha("#ffffff", 0.55)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.letterSpacing: 3
                  font.bold: true
                }
              }

              // Status pill riding the bezel's top edge, clear of the pixels.
              Rectangle {
                anchors.right: parent.right
                anchors.rightMargin: Style.space(12)
                anchors.verticalCenter: parent.top
                height: pillRow.implicitHeight + Style.space(4)
                width: pillRow.implicitWidth + Style.space(10)
                radius: height / 2
                color: "#050505"
                border.width: 1
                border.color: Util.alpha(root.foreground, 0.18)
                visible: root.ready

                Row {
                  id: pillRow
                  anchors.centerIn: parent
                  spacing: Style.space(4)

                  Rectangle {
                    id: liveDot
                    anchors.verticalCenter: parent.verticalCenter
                    width: Style.space(6)
                    height: width
                    radius: width / 2
                    color: root.live ? Color.accent : (root.ready && root.oled.needsUdev ? Color.urgent : Util.alpha("#ffffff", 0.35))

                    SequentialAnimation on opacity {
                      running: root.live && root.opened
                      loops: Animation.Infinite
                      onRunningChanged: if (!running) liveDot.opacity = 1
                      NumberAnimation { to: 0.25; duration: 700 }
                      NumberAnimation { to: 1; duration: 700 }
                    }
                  }

                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: root.live
                      ? (root.oled.idleSyncActive ? "IDLE" : "LIVE")
                      : (root.ready ? root.oled.statusLabel.toUpperCase() : "")
                    color: Util.alpha("#ffffff", 0.8)
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    font.letterSpacing: 1.5
                    font.bold: true
                  }
                }
              }
            }
          }

          // ── Now playing + dice ────────────────────────────────────
          Item {
            width: parent.width
            height: Math.max(nowPlaying.implicitHeight, diceButton.height)

            Column {
              id: nowPlaying
              anchors.left: parent.left
              anchors.right: invertButton.left
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(1)

              Text {
                textFormat: Text.PlainText
                text: root.ready && root.oled.idleSyncActive ? "FOLLOWING SCREENSAVER" : "NOW PLAYING"
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                font.letterSpacing: 1.5
                font.bold: true
              }

              Text {
                width: parent.width
                textFormat: Text.PlainText
                elide: Text.ElideRight
                text: root.ready
                  ? (root.oled.idleSyncActive ? "All effects, shuffled" : root.oled.sourceLabel)
                  : ""
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.heading
                font.bold: true
              }
            }

            Button {
              id: invertButton
              anchors.right: diceButton.left
              anchors.rightMargin: Style.space(6)
              anchors.verticalCenter: parent.verticalCenter
              iconText: ""
              tooltipText: root.ready && root.oled.invert ? "Inverted — click to restore (I)" : "Invert black and white (I)"
              selected: root.ready && root.oled.invert
              bordered: true
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: if (root.ready) root.oled.toggleInvert()
            }

            Button {
              id: diceButton
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              iconText: ""
              text: "Surprise me"
              bordered: true
              tooltipText: ({
                logo: "Random logo style (R)",
                effects: "Random screensaver effect (R)",
                images: "Random GIF from the library (R)",
                text: "Random style for your text (R)"
              })[root.tab] || "Surprise me (R)"
              foreground: root.foreground
              fontFamily: root.fontFamily
              iconRotation: 0
              onClicked: {
                if (!root.ready) return
                root.oled.surpriseMe(root.tab)
                spin.restart()
              }

              RotationAnimation on iconRotation {
                id: spin
                running: false
                from: 0
                to: 360
                duration: 450
                easing.type: Easing.OutBack
              }
            }
          }

          // ── Source tabs ───────────────────────────────────────────
          ButtonGroup {
            width: parent.width
            options: root.tabs
            value: root.tab
            foreground: root.foreground
            fontFamily: root.fontFamily
            onChanged: function(v) { root.tab = v }
          }

          // Logo: the bundled wordmark cycle.
          Column {
            width: parent.width
            spacing: Style.space(8)
            visible: root.tab === "logo"

            Flow {
              width: parent.width
              spacing: Style.space(6)

              Repeater {
                model: root.ready ? root.oled.bundledOrder : []
                delegate: Button {
                  required property var modelData
                  text: root.oled.bundledLabels[modelData] || modelData
                  selected: root.ready && root.oled.preset === modelData
                  bordered: true
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onClicked: if (root.ready) root.oled.setPreset(modelData)
                }
              }
            }

            Text {
              width: parent.width
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              text: "The official wordmark: typed out, still, spun in 3D, or riding a wave. Screensaver (all) plays every effect shuffled."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }

          // Effects: every ttfx screensaver effect as a chip.
          Column {
            width: parent.width
            spacing: Style.space(8)
            visible: root.tab === "effects"

            Row {
              width: parent.width
              spacing: Style.space(6)

              TextField {
                id: effectFilter
                width: parent.width - shuffleAllButton.width - parent.spacing
                placeholderText: "Search 37 effects…"
                foreground: root.foreground
                font.family: root.fontFamily
                Keys.onEscapePressed: text = ""
              }

              Button {
                id: shuffleAllButton
                iconText: ""
                text: "All"
                tooltipText: "Every effect, shuffled — like the real screensaver"
                selected: root.ready && root.oled.preset === "screensaver"
                bordered: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: if (root.ready) root.oled.setPreset("screensaver")
              }
            }

            Flickable {
              id: effectScroll
              width: parent.width
              height: Math.min(effectFlow.implicitHeight, Style.space(200))
              contentWidth: width
              contentHeight: effectFlow.implicitHeight
              clip: true
              boundsBehavior: Flickable.StopAtBounds
              interactive: contentHeight > height

              Flow {
                id: effectFlow
                width: effectScroll.width
                spacing: Style.space(5)

                Repeater {
                  model: {
                    if (!root.ready) return []
                    var q = effectFilter.text.trim().toLowerCase()
                    var all = root.oled.screensaverEffects
                    var out = []
                    for (var i = 0; i < all.length; i++) {
                      if (q === "" || root.oled.effectLabel(all[i]).toLowerCase().indexOf(q) !== -1)
                        out.push(all[i])
                    }
                    return out
                  }
                  delegate: Button {
                    required property var modelData
                    text: root.oled.effectLabel(modelData)
                    selected: root.ready && root.oled.preset === "screensaver:" + modelData
                    bordered: true
                    fontSize: Style.font.bodySmall
                    horizontalPadding: Style.space(8)
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    onClicked: if (root.ready) root.oled.setPreset("screensaver:" + modelData)
                  }
                }
              }
            }

            Text {
              visible: effectFlow.children.length <= 1
              textFormat: Text.PlainText
              text: "No effect matches “" + effectFilter.text + "”"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }

          // Images: bundled/online gifs plus your own imports.
          Column {
            width: parent.width
            spacing: Style.space(8)
            visible: root.tab === "images"

            PanelSectionHeader {
              text: "GIF LIBRARY"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Row {
              width: parent.width
              spacing: Style.space(6)

              TextField {
                id: gifFilter
                width: parent.width - nextGifButton.width - parent.spacing
                placeholderText: "Search " + (root.ready ? root.oled.webGifs.length : "") + " GIFs…"
                foreground: root.foreground
                font.family: root.fontFamily
                Keys.onEscapePressed: text = ""
              }

              Button {
                id: nextGifButton
                iconText: ""
                text: "Next"
                tooltipText: "Step to the next GIF in the library"
                bordered: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                opacity: root.busy ? 0.5 : 1
                onClicked: if (root.ready && !root.busy) root.oled.fetchWebGif()
              }
            }

            Flickable {
              id: gifScroll
              width: parent.width
              height: Math.min(gifFlow.implicitHeight, Style.space(200))
              contentWidth: width
              contentHeight: gifFlow.implicitHeight
              clip: true
              boundsBehavior: Flickable.StopAtBounds
              interactive: contentHeight > height

              Flow {
                id: gifFlow
                width: gifScroll.width
                spacing: Style.space(5)

                Repeater {
                  model: {
                    if (!root.ready) return []
                    var q = gifFilter.text.trim().toLowerCase()
                    var all = root.oled.webGifs
                    var out = []
                    for (var i = 0; i < all.length; i++) {
                      if (q === "" || all[i].name.toLowerCase().indexOf(q) !== -1)
                        out.push({ idx: i, name: all[i].name, bundled: !!all[i].path })
                    }
                    return out
                  }
                  delegate: Button {
                    required property var modelData
                    text: modelData.name
                    iconText: modelData.bundled ? "" : ""
                    tooltipText: modelData.bundled ? "Ships with the plugin" : "Fetched from nlog.us"
                    selected: root.ready && root.oled.isCustom && root.oled.webGifIndex === modelData.idx
                    bordered: true
                    fontSize: Style.font.bodySmall
                    horizontalPadding: Style.space(8)
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    opacity: root.busy ? 0.5 : 1
                    onClicked: if (root.ready && !root.busy) root.oled.jumpToWebGif(modelData.idx)
                  }
                }
              }
            }

            Text {
              visible: gifFlow.children.length <= 1
              textFormat: Text.PlainText
              text: "No GIF matches “" + gifFilter.text + "”"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            PanelSectionHeader {
              text: "YOUR OWN"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Row {
              width: parent.width
              spacing: Style.space(6)

              Button {
                width: root.ready && root.oled.hasCustom ? (parent.width - parent.spacing) / 2 : parent.width
                iconText: ""
                text: root.busy ? "Importing…" : "Choose image"
                tooltipText: "GIF, PNG, JPG, WebP or BMP — resized to 128×40, 1-bit"
                bordered: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: {
                  if (!root.ready || root.busy) return
                  root.close()
                  root.oled.pickImage()
                }
              }

              Button {
                visible: root.ready && root.oled.hasCustom
                width: (parent.width - parent.spacing) / 2
                iconText: ""
                text: "Last image"
                selected: root.ready && root.oled.isCustom
                bordered: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: if (root.ready && !root.oled.isCustom) root.oled.useCustom()
              }
            }

            Column {
              width: parent.width
              spacing: Style.space(4)
              visible: root.ready && root.oled.isCustom

              Item {
                width: parent.width
                height: contrastLabel.implicitHeight

                Text {
                  id: contrastLabel
                  textFormat: Text.PlainText
                  text: "Contrast"
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }

                Text {
                  anchors.right: parent.right
                  textFormat: Text.PlainText
                  text: (root.ready ? root.oled.threshold : 50) + "%"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                }
              }

              PanelSlider {
                width: parent.width
                bar: root.bar
                value: root.ready ? root.oled.threshold : 50
                minimum: 10
                maximum: 90
                step: 5
                integer: true
                onReleased: function(v) { if (root.ready && root.oled.isCustom) root.oled.setThreshold(v) }
              }
            }
          }

          // Text: type anything, pick a font and style.
          Column {
            width: parent.width
            spacing: Style.space(8)
            visible: root.tab === "text"

            Row {
              width: parent.width
              spacing: Style.space(6)

              TextField {
                id: textInput
                width: parent.width - renderButton.width - parent.spacing
                placeholderText: "Type in Omarchy Block…"
                text: root.ready ? root.oled.customText : ""
                foreground: root.foreground
                font.family: root.fontFamily
                onAccepted: if (root.ready && text.trim() !== "") root.oled.setCustomText(text.trim())
              }

              Button {
                id: renderButton
                iconText: root.busy ? "" : ""
                text: root.busy ? "…" : (root.ready && root.oled.isText && textInput.text.trim() === root.oled.customText ? "Showing" : "Show")
                selected: root.ready && root.oled.isText && textInput.text.trim() === root.oled.customText
                bordered: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: {
                  if (!root.ready || root.busy) return
                  var typed = textInput.text.trim()
                  if (typed === "") return
                  if (!root.oled.hasText || typed !== root.oled.customText) root.oled.setCustomText(typed)
                  else root.oled.useText()
                }
              }
            }

            PanelSectionHeader {
              text: "STYLE"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            ButtonGroup {
              enabled: root.ready && root.oled.hasText && !root.busy
              opacity: enabled ? 1 : 0.4
              options: root.ready
                ? root.oled.textStyleOrder.map(function(s) { return { value: s, label: s.charAt(0).toUpperCase() + s.slice(1) } })
                : []
              value: root.ready ? root.oled.textStyle : ""
              foreground: root.foreground
              fontFamily: root.fontFamily
              onChanged: function(v) { if (root.ready) root.oled.setTextStyle(v) }
            }
          }

          PanelSeparator {
            width: parent.width
            foreground: root.foreground
          }

          // ── Tuning ────────────────────────────────────────────────
          Row {
            width: parent.width
            spacing: Style.space(6)

            Button {
              width: parent.width
              iconText: ""
              text: "Follow screensaver"
              tooltipText: "Play the screensaver effects while Omarchy's screensaver is up"
              selected: root.ready && root.oled.idleSync
              bordered: true
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: if (root.ready) root.oled.toggleIdleSync()
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(4)

            Text {
              textFormat: Text.PlainText
              text: "Sleep when idle"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            ButtonGroup {
              width: parent.width
              options: [
                { value: "0", label: "Never" },
                { value: "5", label: "5 min" },
                { value: "10", label: "10 min" },
                { value: "30", label: "30 min" },
                { value: "60", label: "1 hr" }
              ]
              value: root.ready ? String(root.oled.sleepMinutes) : "10"
              fontSize: Style.font.bodySmall
              foreground: root.foreground
              fontFamily: root.fontFamily
              onChanged: function(v) { if (root.ready) root.oled.setSleepMinutes(Number(v)) }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(4)

            Item {
              width: parent.width
              height: speedLabel.implicitHeight

              Text {
                id: speedLabel
                textFormat: Text.PlainText
                text: "Speed"
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Text {
                anchors.right: parent.right
                textFormat: Text.PlainText
                // Lower delay = faster; show it as frames per second too.
                text: {
                  var ms = root.ready ? root.oled.delayMs : 100
                  return ms + " ms · " + Math.round(1000 / ms) + " fps"
                }
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                font.bold: true
              }
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
          }

          // ── Access / errors ───────────────────────────────────────
          Button {
            visible: root.ready && root.oled.needsUdev
            width: parent.width
            iconText: ""
            text: (root.ready && root.oled.udevBusy) ? "Waiting…" : "Allow keyboard access (U)"
            bordered: true
            foreground: root.foreground
            fontFamily: root.fontFamily
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

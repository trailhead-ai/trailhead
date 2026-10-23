(function () {
  "use strict";

  var FONT_FAMILY = "Architects Daughter";
  var root = document.getElementById("reference");
  var notice = document.getElementById("pad-font-notice");

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function fail(reason) {
    root.textContent = "";
    var line = el("p", null, "This page could not be drawn: " + reason);
    line.id = "pad-failure";
    root.appendChild(line);
  }

  function card(entry) {
    var article = el("article", "pad-card ref-card");
    var panel = el("section", "pad-question");
    panel.appendChild(el("p", "ref-round", entry.roundTitle));
    panel.appendChild(el("h2", "pad-prompt", entry.prompt));
    var head = el("header", "pad-card-head");
    head.appendChild(el("span", "pad-letter", entry.letter));
    head.appendChild(el("h3", "pad-name", entry.name));
    panel.appendChild(head);
    panel.appendChild(el("p", "pad-cost", entry.cost));

    var oIndex = entry.letter.charCodeAt(0) - 65;
    var drawing = el("div", "pad-drawing");
    drawing.setAttribute("data-question", String(entry.question - 1));
    drawing.setAttribute("data-option", String(oIndex));
    var ink = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    ink.setAttribute("class", "pad-ink");
    ink.setAttribute("aria-hidden", "true");
    drawing.appendChild(ink);
    panel.appendChild(drawing);
    article.appendChild(panel);

    var option = {};
    if (Object.prototype.hasOwnProperty.call(entry, "flow")) option.flow = entry.flow;
    else option.layout = entry.layout;
    var options = [];
    options[oIndex] = option;
    return { node: article, panel: panel, question: { options: options }, qIndex: entry.question - 1 };
  }

  function fontLoaded() {
    var loaded = false;
    document.fonts.forEach(function (face) {
      if (face.family.replace(/["']/g, "") === FONT_FAMILY && face.status === "loaded") loaded = true;
    });
    return loaded;
  }

  function settleFonts() {
    return document.fonts
      .load('16px "' + FONT_FAMILY + '"')
      .catch(function () {})
      .then(function () { return document.fonts.ready; })
      .then(function () { notice.hidden = fontLoaded(); });
  }

  function build(data) {
    document.title = data.title;
    root.textContent = "";
    var head = el("header", "ref-head");
    head.appendChild(el("h1", null, data.title));
    var chosen = el("p", "ref-chosen", "These drawings were chosen on a scratch pad on " + data.date + ". They are sketches.");
    chosen.id = "ref-chosen";
    head.appendChild(chosen);
    root.appendChild(head);
    var list = el("div", "ref-cards");
    var cards = data.drawings.map(card);
    cards.forEach(function (c) { list.appendChild(c.node); });
    root.appendChild(list);
    return cards;
  }

  function finish() {
    document.documentElement.setAttribute("data-pad-ready", "");
  }

  var data;
  try {
    data = window.PAD_REFERENCE;
    if (!data || !Array.isArray(data.drawings)) throw new Error("the reference data did not load.");
  } catch (error) {
    fail(String((error && error.message) || error));
    finish();
    return;
  }

  var cards = build(data);
  var pen = window.PadPen;
  var drawn = cards.map(function (c) { return pen.drawQuestion(c.panel, c.question, c.qIndex); });
  Promise.all(drawn).catch(function () {}).then(settleFonts).catch(function () {}).then(finish);
})();

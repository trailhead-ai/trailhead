(function () {
  "use strict";

  var FONT_FAMILY = "Architects Daughter";
  var root = document.getElementById("pad");
  var notice = document.getElementById("pad-font-notice");

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function fail(reason) {
    root.textContent = "";
    var line = el("p", null, "This pad could not be drawn: " + reason);
    line.id = "pad-failure";
    root.appendChild(line);
  }

  function reasonOf(error) {
    return String((error && error.message) || error);
  }

  function renderOption(option, qIndex, oIndex) {
    var card = el("article", "pad-card");
    var head = el("header", "pad-card-head");
    head.appendChild(el("span", "pad-letter", String.fromCharCode(65 + oIndex)));
    head.appendChild(el("h3", "pad-name", option.name));
    if (option.pick === true) head.appendChild(el("span", "pad-pick", "my pick"));
    card.appendChild(head);
    card.appendChild(el("p", "pad-cost", option.cost));

    var drawing = el("div", "pad-drawing");
    drawing.setAttribute("data-question", String(qIndex));
    drawing.setAttribute("data-option", String(oIndex));
    var ink = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    ink.setAttribute("class", "pad-ink");
    ink.setAttribute("aria-hidden", "true");
    drawing.appendChild(ink);
    card.appendChild(drawing);
    return card;
  }

  function renderQuestion(question, qIndex) {
    var panel = el("section", "pad-question");
    panel.appendChild(el("h2", "pad-prompt", question.prompt));
    var cards = el("div", "pad-cards");
    question.options.forEach(function (option, oIndex) {
      cards.appendChild(renderOption(option, qIndex, oIndex));
    });
    panel.appendChild(cards);
    return panel;
  }

  function drawWithPen(panel, question, qIndex) {
    if (window.PadPen && typeof window.PadPen.drawQuestion === "function") {
      return window.PadPen.drawQuestion(panel, question, qIndex);
    }
    var drawings = panel.querySelectorAll(".pad-drawing");
    for (var i = 0; i < drawings.length; i++) {
      var box = el("div", "pen-message");
      ["Can't draw this layout:", "The sketch pen did not load."].forEach(function (line) {
        box.appendChild(el("p", null, line));
      });
      drawings[i].textContent = "";
      drawings[i].appendChild(box);
    }
    return undefined;
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
    var head = el("header", "pad-head");
    head.appendChild(el("h1", null, data.title));
    if (typeof data.intro === "string" && data.intro) head.appendChild(el("p", "pad-intro", data.intro));

    var list = el("ul");
    var stage = el("div", "pad-stage");
    var buttons = [];
    var panels = [];

    function show(index) {
      buttons.forEach(function (button, i) {
        if (i === index) button.setAttribute("aria-current", "true");
        else button.removeAttribute("aria-current");
      });
      var fresh = !panels[index];
      if (fresh) {
        panels[index] = renderQuestion(data.questions[index], index);
        stage.appendChild(panels[index]);
      }
      panels.forEach(function (panel, i) { if (panel) panel.hidden = i !== index; });
      return fresh ? drawWithPen(panels[index], data.questions[index], index) : undefined;
    }

    data.questions.forEach(function (question, index) {
      var item = el("li");
      var button = el("button", null, question.prompt);
      button.type = "button";
      button.addEventListener("click", function () {
        Promise.resolve()
          .then(function () { return show(index); })
          .catch(function (error) { fail(reasonOf(error)); });
      });
      buttons.push(button);
      item.appendChild(button);
      list.appendChild(item);
    });

    var nav = el("nav");
    nav.setAttribute("aria-label", "Questions");
    nav.className = "pad-questions";
    nav.appendChild(list);
    var body = el("div", "pad-body");
    body.appendChild(nav);
    body.appendChild(stage);

    root.textContent = "";
    root.appendChild(head);
    root.appendChild(body);
    return show(0);
  }

  function run() {
    var data = window.PAD_ROUND;
    if (data === undefined) throw new Error("the round's data did not load.");
    if (!data || typeof data.title !== "string" || !Array.isArray(data.questions) || !data.questions.length) {
      throw new Error("the round's data does not define a round.");
    }
    return build(data);
  }

  var rendered;
  try {
    rendered = Promise.resolve(run());
  } catch (error) {
    rendered = Promise.reject(error);
  }
  rendered
    .catch(function (error) { fail(reasonOf(error)); })
    .then(settleFonts)
    .catch(function () {})
    .then(function () { document.documentElement.setAttribute("data-pad-ready", ""); });
})();

(function () {
  "use strict";

  var FONT_FAMILY = "Architects Daughter";
  var root = document.getElementById("pad");
  var notice = document.getElementById("pad-font-notice");
  var cantKeep = document.getElementById("pad-cant-keep");
  var NOTE_LIMIT = 10000;

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

  function optionLetter(index) {
    return String.fromCharCode(65 + index);
  }

  function toggle(label, option, oIndex, onPress) {
    var button = el("button", "pad-toggle", label);
    button.type = "button";
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-label", label + ": " + optionLetter(oIndex) + ", " + option.name);
    button.addEventListener("click", onPress);
    return button;
  }

  function renderOption(option, qIndex, oIndex, act, view) {
    var card = el("article", "pad-card");
    var head = el("header", "pad-card-head");
    head.appendChild(el("span", "pad-letter", optionLetter(oIndex)));
    head.appendChild(el("h3", "pad-name", option.name));
    if (option.pick === true) head.appendChild(el("span", "pad-pick", "my pick"));
    var mark = el("span", "pad-mark");
    mark.hidden = true;
    head.appendChild(mark);
    card.appendChild(head);

    var choices = el("div", "pad-choices");
    var pickButton = toggle("This one", option, oIndex, function () { act(qIndex, "pick", oIndex); });
    var rejectButton = toggle("Not this", option, oIndex, function () { act(qIndex, "reject", oIndex); });
    choices.appendChild(pickButton);
    choices.appendChild(rejectButton);
    card.appendChild(choices);
    view.cards.push({ card: card, mark: mark, pick: pickButton, reject: rejectButton });
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

  function renderQuestion(question, qIndex, act, view) {
    var panel = el("section", "pad-question");
    panel.appendChild(el("h2", "pad-prompt", question.prompt));
    var cards = el("div", "pad-cards");
    question.options.forEach(function (option, oIndex) {
      cards.appendChild(renderOption(option, qIndex, oIndex, act, view));
    });
    panel.appendChild(cards);

    var noneButton = el("button", "pad-toggle pad-none", "None of these");
    noneButton.type = "button";
    noneButton.setAttribute("aria-pressed", "false");
    noneButton.addEventListener("click", function () { act(qIndex, "none"); });
    view.none = noneButton;
    panel.appendChild(noneButton);

    var noteId = "pad-note-" + qIndex;
    var noteLabel = el("label", "pad-note-label", "Note");
    noteLabel.setAttribute("for", noteId);
    var noteBox = el("textarea", "pad-note");
    noteBox.id = noteId;
    noteBox.rows = 3;
    noteBox.maxLength = NOTE_LIMIT;
    noteBox.addEventListener("input", function () { act(qIndex, "note", noteBox.value); });
    view.note = noteBox;
    panel.appendChild(noteLabel);
    panel.appendChild(noteBox);
    return panel;
  }

  function drawWithPen(panel, question, qIndex) {
    if (window.PadPen && typeof window.PadPen.drawQuestion === "function") {
      return window.PadPen.drawQuestion(panel, question, qIndex);
    }
    var drawings = panel.querySelectorAll(".pad-drawing");
    for (var i = 0; i < drawings.length; i++) {
      var box = el("div", "pen-message");
      var option = question && question.options ? question.options[Number(drawings[i].getAttribute("data-option"))] : undefined;
      var kind = option && typeof option === "object" && Object.prototype.hasOwnProperty.call(option, "flow") ? "flow" : "layout";
      ["Can't draw this " + kind + ":", "The sketch pen did not load."].forEach(function (line) {
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

  var REPLY_UNBUILT = "The reply could not be built. Answer in words in your session instead.";
  var COPIED = "Copied. Paste it into your session.";
  var BLOCKED = "Your browser blocked copying. The reply is selected \u2014 press Ctrl+C (\u2318C on a Mac).";

  function summaryOf(question, answer) {
    var head = question.prompt + " \u2014 ";
    if (answer.pick !== null) return head + window.PadReply.letter(answer.pick) + ", " + question.options[answer.pick].name;
    if (answer.none) return head + "none of these";
    var said = [];
    if (answer.not.length) said.push("not " + answer.not.map(window.PadReply.letter).join(", "));
    if (answer.note) said.push("has a note");
    return said.length ? head + "no pick yet (" + said.join("; ") + ")" : head + "no answer yet";
  }

  function buildReplyStep(data, answers) {
    var panel = el("section", "pad-reply");
    panel.id = "pad-reply";
    panel.hidden = true;
    panel.appendChild(el("h2", "pad-reply-title", "Your reply"));
    var unbuilt = el("p", "pad-reply-unbuilt", REPLY_UNBUILT);
    var summary = el("ul", "pad-reply-summary");
    var unanswered = el("p", "pad-reply-unanswered");
    var label = el("label", "pad-note-label", "Your reply");
    label.setAttribute("for", "pad-reply-box");
    var box = el("textarea", "pad-reply-box");
    box.id = "pad-reply-box";
    box.readOnly = true;
    box.rows = 14;
    var copyButton = el("button", "pad-toggle pad-copy", "Copy reply");
    copyButton.type = "button";
    var status = el("p", "pad-reply-status");
    status.setAttribute("role", "status");
    [unbuilt, summary, unanswered, label, box, copyButton, status].forEach(function (node) { panel.appendChild(node); });

    var live = [summary, unanswered, label, box, copyButton, status];

    function fill() {
      var reply;
      var lines = [];
      var missing = 0;
      try {
        reply = window.PadReply.build(data, answers);
        data.questions.forEach(function (question, i) {
          lines.push(summaryOf(question, answers[i]));
          if (answers[i].pick === null && !answers[i].none) missing++;
        });
      } catch (error) {
        reply = undefined;
      }
      unbuilt.hidden = reply !== undefined;
      live.forEach(function (node) { node.hidden = reply === undefined; });
      if (reply === undefined) return;
      summary.textContent = "";
      lines.forEach(function (line) { summary.appendChild(el("li", null, line)); });
      unanswered.hidden = missing === 0;
      unanswered.textContent = missing === 0 ? "" :
        "No pick yet on " + missing + " of " + data.questions.length +
        (data.questions.length === 1 ? " question" : " questions") + ". You can still copy the reply.";
      box.value = reply;
    }

    function blocked() {
      box.focus();
      box.select();
      box.setSelectionRange(0, box.value.length);
      status.textContent = BLOCKED;
    }

    copyButton.addEventListener("click", function () {
      var written;
      try {
        written = navigator.clipboard.writeText(box.value);
      } catch (error) {
        return blocked();
      }
      Promise.resolve(written).then(function () { status.textContent = COPIED; }, blocked);
    });

    return {
      panel: panel,
      show: fill,
      changed: function () { status.textContent = ""; fill(); }
    };
  }

  var TITLE_LIMIT = 40;

  function knownRounds() {
    var rounds = window.PAD_ROUNDS;
    var number = window.PAD_ROUND_NUMBER;
    if (!Array.isArray(rounds) || rounds.length < 2) return null;
    var readable = rounds.every(function (round) {
      return round && typeof round.title === "string";
    });
    if (!readable || !(number >= 1 && number <= rounds.length && Math.floor(number) === number)) return null;
    return { titles: rounds.map(function (round) { return round.title; }), current: number };
  }

  function roundLink(number, label, title) {
    var link = el("a", "pad-round-link");
    link.href = "../round-" + number + "/index.html";
    link.target = "_blank";
    link.rel = "noopener";
    if (title !== undefined) link.title = title;
    link.appendChild(el("span", "pad-round-text", label));
    link.appendChild(el("span", "pad-sr", " (opens in a new tab)"));
    return link;
  }

  function roundLabel(number, title) {
    var shown = title.length > TITLE_LIMIT ? title.slice(0, TITLE_LIMIT).replace(/\s+$/, "") + "\u2026" : title;
    return "Round " + number + ": " + shown;
  }

  function renderSwitcher(rounds) {
    var nav = el("nav", "pad-rounds");
    nav.setAttribute("aria-label", "Rounds");
    nav.appendChild(el("span", "pad-rounds-label", "Rounds:"));
    var list = el("ul");
    rounds.titles.forEach(function (title, i) {
      var number = i + 1;
      var item = el("li");
      if (number === rounds.current) {
        var here = el("span", "pad-round-current");
        here.setAttribute("aria-current", "page");
        here.title = title;
        here.appendChild(el("span", "pad-round-text", roundLabel(number, title)));
        item.appendChild(here);
      } else {
        item.appendChild(roundLink(number, roundLabel(number, title), title));
      }
      list.appendChild(item);
    });
    nav.appendChild(list);
    return nav;
  }

  function renderRoundNote(rounds) {
    if (rounds.current === rounds.titles.length) return null;
    var latest = rounds.titles.length;
    var note = el("p", "pad-rounds-note");
    note.appendChild(document.createTextNode("This is round " + rounds.current + " of " + latest + ". "));
    note.appendChild(roundLink(latest, "Go to the latest round"));
    return note;
  }

  function renderSettled(settled) {
    if (!Array.isArray(settled)) return null;
    var lines = settled.filter(function (line) { return typeof line === "string" && line; });
    if (!lines.length) return null;
    var fold = el("details", "pad-settled");
    fold.appendChild(el("summary", null, "Settled in earlier rounds (" + lines.length + ")"));
    var list = el("ul");
    lines.forEach(function (line) { list.appendChild(el("li", null, line)); });
    fold.appendChild(list);
    return fold;
  }

  function build(data) {
    var head = el("header", "pad-head");
    var rounds = knownRounds();
    if (rounds) {
      head.appendChild(renderSwitcher(rounds));
      var note = renderRoundNote(rounds);
      if (note) head.appendChild(note);
    }
    head.appendChild(el("h1", null, data.title));
    if (typeof data.intro === "string" && data.intro) head.appendChild(el("p", "pad-intro", data.intro));
    var settled = renderSettled(data.settled);
    if (settled) head.appendChild(settled);

    var list = el("ul");
    var stage = el("div", "pad-stage");
    var buttons = [];
    var dots = [];
    var panels = [];
    var views = [];
    var answers = data.questions.map(function () {
      return { pick: null, not: [], none: false, note: "" };
    });
    var hook = function () {};

    function refresh(qIndex) {
      var answer = answers[qIndex];
      var view = views[qIndex];
      var answered = answer.pick !== null || answer.none;
      var dot = dots[qIndex];
      dot.setAttribute("data-answered", String(answered));
      dot.firstChild.textContent = answered ? "answered" : "not answered";
      if (!view) return;
      view.cards.forEach(function (c, oIndex) {
        var picked = answer.pick === oIndex;
        var rejected = answer.not.indexOf(oIndex) !== -1;
        c.pick.setAttribute("aria-pressed", String(picked));
        c.reject.setAttribute("aria-pressed", String(rejected));
        c.mark.textContent = picked ? "picked" : rejected ? "not this" : "";
        c.mark.hidden = !(picked || rejected);
      });
      view.none.setAttribute("aria-pressed", String(answer.none));
      if (view.note.value !== answer.note) view.note.value = answer.note;
    }

    function change(qIndex, kind, value) {
      var answer = answers[qIndex];
      var at = answer.not.indexOf(value);
      if (kind === "pick") {
        if (answer.pick === value) {
          answer.pick = null;
        } else {
          answer.pick = value;
          answer.none = false;
          if (at !== -1) answer.not.splice(at, 1);
        }
      } else if (kind === "reject") {
        if (at !== -1) {
          answer.not.splice(at, 1);
        } else {
          answer.not.push(value);
          answer.not.sort(function (a, b) { return a - b; });
          if (answer.pick === value) answer.pick = null;
        }
      } else if (kind === "none") {
        answer.none = !answer.none;
        if (answer.none) answer.pick = null;
      } else {
        answer.note = value;
      }
      refresh(qIndex);
      hook(qIndex);
      keep();
    }

    window.PadAnswers = {
      answers: answers,
      onChange: function (fn) { hook = fn; }
    };

    var reply = buildReplyStep(data, answers);
    window.PadAnswers.onChange(reply.changed);

    var keeper = null;
    var restored = null;
    var roundId = window.PAD_ROUND_ID;
    if (window.PadSaved && typeof roundId === "string" && roundId) {
      keeper = window.PadSaved.create(roundId, data.questions.map(function (question) { return question.options.length; }));
      restored = keeper.read();
      if (!restored.usable) keeper = null;
    }

    function keep() {
      if (keeper && !keeper.write(answers)) cantKeep.hidden = false;
    }

    function applyAnswers(list) {
      list.forEach(function (saved, qIndex) {
        var answer = answers[qIndex];
        answer.pick = saved.pick;
        answer.none = saved.none;
        answer.not.length = 0;
        saved.not.forEach(function (n) { answer.not.push(n); });
        var view = views[qIndex];
        answer.note = view && view.note === document.activeElement ? view.note.value : saved.note;
        if (dots[qIndex]) refresh(qIndex);
      });
      reply.changed();
    }

    if (!keeper) {
      cantKeep.hidden = false;
    } else {
      if (restored.answers) applyAnswers(restored.answers);
      keeper.watch(applyAnswers);
    }

    function setCurrent(index) {
      buttons.forEach(function (button, i) {
        if (i === index) button.setAttribute("aria-current", "true");
        else button.removeAttribute("aria-current");
      });
    }

    function showReply() {
      setCurrent(buttons.length - 1);
      panels.forEach(function (panel) { if (panel) panel.hidden = true; });
      reply.show();
      reply.panel.hidden = false;
    }

    function show(index) {
      setCurrent(index);
      reply.panel.hidden = true;
      var fresh = !panels[index];
      if (fresh) {
        views[index] = { cards: [] };
        panels[index] = renderQuestion(data.questions[index], index, change, views[index]);
        refresh(index);
        stage.appendChild(panels[index]);
      }
      panels.forEach(function (panel, i) { if (panel) panel.hidden = i !== index; });
      if (!fresh && window.PadPen && typeof window.PadPen.shown === "function") window.PadPen.shown(panels[index]);
      return fresh ? drawWithPen(panels[index], data.questions[index], index) : undefined;
    }

    data.questions.forEach(function (question, index) {
      var item = el("li");
      var button = el("button");
      var dot = el("span", "pad-dot");
      dot.appendChild(el("span", "pad-sr"));
      var label = el("span", "pad-entry-label", question.prompt);
      button.appendChild(dot);
      button.appendChild(label);
      button.type = "button";
      dots.push(dot);
      refresh(index);
      button.addEventListener("click", function () {
        Promise.resolve()
          .then(function () { return show(index); })
          .catch(function (error) { fail(reasonOf(error)); });
      });
      buttons.push(button);
      item.appendChild(button);
      list.appendChild(item);
    });

    var replyItem = el("li", "pad-reply-item");
    var replyButton = el("button", "pad-reply-entry");
    replyButton.type = "button";
    replyButton.appendChild(el("span", "pad-entry-label", "Your reply"));
    replyButton.addEventListener("click", showReply);
    buttons.push(replyButton);
    replyItem.appendChild(replyButton);
    list.appendChild(replyItem);

    var nav = el("nav");
    nav.setAttribute("aria-label", "Questions");
    nav.className = "pad-questions";
    nav.appendChild(list);
    var body = el("div", "pad-body");
    body.appendChild(nav);
    body.appendChild(stage);
    stage.appendChild(reply.panel);

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

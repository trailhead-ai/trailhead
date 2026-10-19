(function () {
  "use strict";

  function letter(index) {
    return String.fromCharCode(65 + index);
  }

  var HIDDEN = /[\p{Cf}\p{Zl}\p{Zp}\u0080-\u009f]/gu;

  function escapeHidden(json) {
    return json.replace(HIDDEN, function (ch) {
      var out = "";
      for (var i = 0; i < ch.length; i++) {
        out += "\\u" + ("0000" + ch.charCodeAt(i).toString(16)).slice(-4);
      }
      return out;
    });
  }

  function build(round, answers) {
    var reply = { pad: round.title };
    var number = window.PAD_ROUND_NUMBER;
    if (typeof number === "number" && isFinite(number)) reply.round = number;
    reply.answers = round.questions.map(function (question, i) {
      var answer = answers[i];
      var rejected = answer.not.filter(function (n, at, all) {
        return all.indexOf(n) === at;
      });
      rejected.sort(function (a, b) {
        return a - b;
      });
      return {
        question: question.prompt,
        pick: answer.pick === null ? null : letter(answer.pick),
        not: rejected.map(letter),
        none: answer.none,
        note: answer.note
      };
    });
    return "```json\n" + escapeHidden(JSON.stringify(reply, null, 2)) + "\n```";
  }

  window.PadReply = { build: build, letter: letter };
})();

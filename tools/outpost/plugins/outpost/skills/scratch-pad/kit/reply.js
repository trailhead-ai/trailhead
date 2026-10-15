(function () {
  "use strict";

  function letter(index) {
    return String.fromCharCode(65 + index);
  }

  function build(round, answers) {
    var reply = {
      pad: round.title,
      answers: round.questions.map(function (question, i) {
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
      })
    };
    return "```json\n" + JSON.stringify(reply, null, 2) + "\n```";
  }

  window.PadReply = { build: build, letter: letter };
})();

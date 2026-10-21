// PadSaved keeps one round's answers in the browser's localStorage and hands back only
// entries that fit the round exactly.
//
// Contract. It is given the round's id (a non-empty string) and the round's shape: an array
// with one number per question, the number of options that question has. It reads no page
// globals and knows nothing about drawings, so any page that keeps answers in the same shape
// can use it.
//
// The answer shape, one per question in question order:
//   { pick: <option index or null>, not: [<distinct option indexes, never the pick>],
//     none: <true or false, false when there is a pick>, note: <string, at most 10000 characters> }
//
// The stored entry is JSON under "outpost-scratch-pad:<round id>": { "answers": [<answer>, ...] }.
//
//   var keeper = PadSaved.create(roundId, [3, 3]);
//   keeper.read()          -> { usable: false }                    reading storage threw
//                             { usable: true, answers: null }      nothing stored, or the entry was
//                                                                  ignored (one console.warn)
//                             { usable: true, answers: [...] }     a stored entry that fits the round
//   keeper.accept(text)    -> the answers list if text is an entry that fits the round, else null
//                             with one console.warn naming the round and the failed check
//   keeper.write(answers)  -> true if the whole entry was stored, false if the write threw
(function () {
  "use strict";

  var PREFIX = "outpost-scratch-pad:";
  var NOTE_LIMIT = 10000;
  var ESCAPED_CHAR = 6;
  var has = Object.prototype.hasOwnProperty;

  function wholeNumber(value) {
    return Number.isInteger(value);
  }

  function exactKeys(value, keys) {
    if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
    var own = Object.keys(value);
    return own.length === keys.length && keys.every(function (key) { return has.call(value, key); });
  }

  function entryLimit(optionCounts) {
    var total = '{"answers":[]}'.length + optionCounts.length;
    optionCounts.forEach(function (count) {
      total += '{"pick":,"not":[],"none":false,"note":""}'.length + String(count).length +
        count * (String(count).length + 1) + NOTE_LIMIT * ESCAPED_CHAR;
    });
    return total;
  }

  function failedCheck(entry, optionCounts) {
    if (!exactKeys(entry, ["answers"])) return "top-level keys are not exactly answers";
    var answers = entry.answers;
    if (!Array.isArray(answers) || answers.length !== optionCounts.length) {
      return "answer count is not " + optionCounts.length;
    }
    for (var q = 0; q < answers.length; q++) {
      var answer = answers[q];
      var at = "answer " + q + ": ";
      var options = optionCounts[q];
      if (!exactKeys(answer, ["pick", "not", "none", "note"])) return at + "answer keys are not exactly pick, not, none, note";
      if (answer.pick !== null && !(wholeNumber(answer.pick) && answer.pick >= 0 && answer.pick < options)) {
        return at + "pick is not null or an option number";
      }
      if (!Array.isArray(answer.not)) return at + "not list is not a list";
      for (var i = 0; i < answer.not.length; i++) {
        var n = answer.not[i];
        if (!(wholeNumber(n) && n >= 0 && n < options) || answer.not.indexOf(n) !== i) {
          return at + "not list is not distinct option numbers";
        }
        if (n === answer.pick) return at + "not list holds the pick";
      }
      if (typeof answer.none !== "boolean") return at + "none is not true or false";
      if (answer.none && answer.pick !== null) return at + "none with a pick";
      if (typeof answer.note !== "string" || answer.note.length > NOTE_LIMIT) {
        return at + "note is not a string of at most " + NOTE_LIMIT + " characters";
      }
    }
    return null;
  }

  function create(roundId, optionCounts) {
    var key = PREFIX + roundId;
    var limit = entryLimit(optionCounts);

    function warn(why) {
      try {
        console.warn("scratch pad round " + roundId + ": ignoring saved answers, " + why);
      } catch (ignored) {}
    }

    function accept(text) {
      if (typeof text !== "string") {
        warn("the stored entry is not text");
        return null;
      }
      if (text.length > limit) {
        warn("the stored entry is too long (" + text.length + " characters, at most " + limit + ")");
        return null;
      }
      var entry;
      try {
        entry = JSON.parse(text);
      } catch (error) {
        warn("the stored entry is not JSON");
        return null;
      }
      var failed = failedCheck(entry, optionCounts);
      if (failed) {
        warn(failed);
        return null;
      }
      return entry.answers;
    }

    function read() {
      var text;
      try {
        text = window.localStorage.getItem(key);
      } catch (error) {
        return { usable: false };
      }
      return { usable: true, answers: text === null ? null : accept(text) };
    }

    function write(answers) {
      try {
        window.localStorage.setItem(key, JSON.stringify({ answers: answers }));
        return true;
      } catch (error) {
        return false;
      }
    }

    return { read: read, accept: accept, write: write };
  }

  window.PadSaved = { create: create };
})();

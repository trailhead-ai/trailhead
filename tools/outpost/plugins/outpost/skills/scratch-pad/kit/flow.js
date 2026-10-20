/*
 * The flow contract: parse a flow written in a small part of Mermaid's flowchart syntax, and lay it
 * out in layers. No drawing happens here; pen.js traces what layout() returns.
 *
 *   PadFlow.parse(text)            -> flow, or throws a FlowError
 *   PadFlow.layout(flow, measure)  -> drawing plan
 *
 * flow = {
 *   direction: "LR" | "TD",                       // TB is read as TD
 *   steps:  Map id -> { id, shape, label, labelled, accent }   // shape: box | round | pill | decision
 *   order:  [id, ...]                             // steps in order of first appearance
 *   arrows: [{ index, from, to, dashed, label, loop }]   // index is the written order; ids, not step objects;
 *                                                        // label is the text on the arrow, or null; loop is true
 *                                                        // for a loop back (see findLoops)
 * }
 *
 * plan = {
 *   width, height,                                // the flow's natural size, in SVG units
 *   steps:  [{ id, shape, accent, x, y, w, h, lines: [{ text, x, y }] }]   // x, y: top-left; lines: label centres
 *   arrows: [{ index, from, to, dashed, loop, points: [[x, y], ...],     // from the source edge to the target edge
 *             label: null | { lines: [{ text, x, y }], halo: { x, y, w, h } } }]   // at the first leg's middle
 * }
 *
 * plan.arrows lists the forward arrows in written order, then the loop backs in written order. A loop
 * back is drawn in its own lane outside the flow (below it for LR, right of it for TD); the first one
 * written takes the lane nearest the flow, and its label sits at the middle of its run along the lane.
 * The plan's size includes the lanes.
 *
 * measure(text) returns the width of one line of label text as the browser sets it. Parse and layout
 * are pure functions of the flow text and measure(); nothing depends on the page width. Every table
 * keyed by a step id is a Map, so an id such as __proto__ is an ordinary step.
 *
 * A flow that cannot be drawn throws a FlowError carrying problems: [{ line, text, message }] in line
 * order, every broken line at once. line is 1-based, or null for the whole flow (the flow has no steps,
 * or a limit is passed; those are the only problem listed). message repeats the first problem's.
 */
(function () {
  "use strict";

  var MAX_TEXT = 200;
  var MAX_LABEL_WIDTH = 400;
  var MAX_LINES = 400;
  var MAX_LINE_LENGTH = 1000;
  var MAX_STEPS = 60;
  var MAX_ARROWS = 120;
  var MAX_WAYPOINTS = 2000;
  var LINE_HEIGHT = 21;
  var PAD_X = 14;
  var PAD_Y = 10;
  var GAP_MAIN = 64;
  var GAP_CROSS = 28;
  var LANE = 18;
  var HALO_PAD_X = 4;
  var HALO_PAD_Y = 5;
  var MARGIN = 14;

  function failed(problems) {
    var shown = problems.map(function (p) {
      var text = p.text === null ? null : p.text.length > MAX_TEXT ? p.text.slice(0, MAX_TEXT) + "…" : p.text;
      return { line: p.line, text: text, message: p.message };
    });
    var error = new Error(shown[0].message);
    error.name = "FlowError";
    error.isFlowError = true;
    error.problems = shown;
    return error;
  }

  // A problem with the whole flow rather than one of its lines.
  function wholeFlow(message) {
    return failed([{ line: null, text: null, message: message }]);
  }

  var HEADER = /^(?:flowchart|graph)[ \t]+(LR|TD|TB)$/;
  var ID = /[A-Za-z0-9_]+/y;
  var CLASS_NAME = /:::([A-Za-z0-9_-]*)/y;
  var SHAPES = [
    { open: "([", close: "])", shape: "pill" },
    { open: "(", close: ")", shape: "round" },
    { open: "[", close: "]", shape: "box" },
    { open: "{", close: "}", shape: "decision" },
  ];
  var OTHER_STEPS = [
    { open: "((", reason: 'Circle steps ("((…))") are not drawn; a step is [box], (rounded), ([pill]) or {decision}.' },
    { open: "{{", reason: 'Hexagon steps ("{{…}}") are not drawn; a step is [box], (rounded), ([pill]) or {decision}.' },
    { open: "[(", reason: 'Database steps ("[(…)]") are not drawn; a step is [box], (rounded), ([pill]) or {decision}.' },
    { open: "[[", reason: 'Subroutine steps ("[[…]]") are not drawn; a step is [box], (rounded), ([pill]) or {decision}.' },
    { open: "[/", reason: 'Slanted steps ("[/…/]") are not drawn; a step is [box], (rounded), ([pill]) or {decision}.' },
    { open: "[\\", reason: 'Slanted steps ("[\\…\\]") are not drawn; a step is [box], (rounded), ([pill]) or {decision}.' },
    { open: ">", reason: 'Flag steps (">…]") are not drawn; a step is [box], (rounded), ([pill]) or {decision}.' },
  ];
  var LABEL_FORBIDDEN = /[\[\](){}"|]/;
  var ARROW_HELP = 'this pen draws "-->", "-.->" and their labelled forms.';

  function skipSpaces(s, pos) {
    while (pos < s.length && (s[pos] === " " || s[pos] === "\t")) pos++;
    return pos;
  }

  function readLabel(s, pos, shape) {
    var label;
    if (s[pos] === '"') {
      var end = s.indexOf('"', pos + 1);
      if (end < 0) throw new Error("A quoted label needs a closing quote.");
      label = s.slice(pos + 1, end);
      pos = end + 1;
      if (s.substr(pos, shape.close.length) !== shape.close) {
        throw new Error('A quoted label must be followed by "' + shape.close + '".');
      }
    } else {
      var close = s.indexOf(shape.close, pos);
      if (close < 0) throw new Error('A step label needs its closing "' + shape.close + '".');
      label = s.slice(pos, close).trim();
      pos = close;
      if (LABEL_FORBIDDEN.test(label) || label[0] === "/" || label[0] === "\\") {
        throw new Error("This step shape is not drawn; a label may not hold brackets unless it is quoted.");
      }
    }
    if (label.trim() === "") throw new Error("A step label cannot be empty.");
    return { label: label, end: pos + shape.close.length };
  }

  function readStep(s, pos) {
    ID.lastIndex = pos;
    var m = ID.exec(s);
    if (!m) throw new Error('A step is an id of letters, digits and "_" here.');
    var node = { id: m[0], shape: null, label: null, accent: false };
    pos += m[0].length;
    for (var o = 0; o < OTHER_STEPS.length; o++) {
      if (s.substr(pos, OTHER_STEPS[o].open.length) === OTHER_STEPS[o].open) throw new Error(OTHER_STEPS[o].reason);
    }
    for (var i = 0; i < SHAPES.length; i++) {
      var shape = SHAPES[i];
      if (s.substr(pos, shape.open.length) === shape.open) {
        var read = readLabel(s, pos + shape.open.length, shape);
        node.shape = shape.shape;
        node.label = read.label;
        pos = read.end;
        break;
      }
    }
    CLASS_NAME.lastIndex = pos;
    var mark = CLASS_NAME.exec(s);
    if (mark) {
      node.accent = mark[1] === "accent";
      pos += mark[0].length;
    }
    return { node: node, end: pos };
  }

  function unquoted(text) {
    var t = text.trim();
    return t.length >= 2 && t[0] === '"' && t[t.length - 1] === '"' ? t.slice(1, -1) : t;
  }

  function labelled(text) {
    var label = unquoted(text);
    if (label.trim() === "") throw new Error("An arrow label cannot be empty.");
    return label;
  }

  var ARROW_WORDS = [
    { re: /^-\.->/y, dashed: true, bar: true },
    { re: /^-->/y, dashed: false, bar: true },
    { re: /^-\.[ \t]+(.*?)[ \t]*\.->/y, dashed: true, bar: false },
    { re: /^--[ \t]+(.*?)[ \t]*-->/y, dashed: false, bar: false },
  ];
  var ARROW_REFUSED = [
    { re: /^<(?:-->|-\.->|--|-\.-)/, reason: "Two-way arrows are not drawn; write one arrow each way." },
    { re: /^={2,}>?/, reason: 'Thick arrows ("==>") are not drawn; use "-->".' },
    { re: /^---/, reason: 'Open links ("---") are not drawn; use "-->".' },
    { re: /^-\.-(?!>)/, reason: 'Open links ("-.-") are not drawn; use "-.->" or "-->".' },
    { re: /^--o(?=\s|$)/, reason: 'Circle-ended links ("--o") are not drawn; use "-->".' },
    { re: /^--x(?=\s|$)/, reason: 'Cross-ended links ("--x") are not drawn; use "-->".' },
  ];

  // The arrow starting at pos: its end, whether it is dashed, and its label (or null).
  function readArrow(s, pos) {
    var rest = s.slice(pos);
    for (var r = 0; r < ARROW_REFUSED.length; r++) {
      if (ARROW_REFUSED[r].re.test(rest)) throw new Error(ARROW_REFUSED[r].reason);
    }
    for (var w = 0; w < ARROW_WORDS.length; w++) {
      var word = ARROW_WORDS[w];
      word.re.lastIndex = 0;
      var m = word.re.exec(rest);
      if (!m) continue;
      var end = pos + m[0].length;
      if (!word.bar) return { end: end, dashed: word.dashed, label: labelled(m[1]) };
      if (s[end] !== "|") return { end: end, dashed: word.dashed, label: null };
      var close = s.indexOf("|", end + 1);
      if (close < 0) throw new Error('An arrow label needs its closing "|".');
      return { end: close + 1, dashed: word.dashed, label: labelled(s.slice(end + 1, close)) };
    }
    if (/^[-.=~]/.test(rest)) throw new Error("This is not an arrow this pen draws; " + ARROW_HELP);
    throw new Error("A step is followed by an arrow or the end of the line; " + ARROW_HELP);
  }

  var STYLING = /^(?:(?:classDef|class|style|linkStyle)[ \t]+(?![-=<])|%%\{.*\}%%$)/;

  // A line kind turns one cleaned line into what it contributes, or returns null when the line is
  // not its kind. nodes are the steps the line names in order; links[k] is the arrow from nodes[k] to
  // nodes[k + 1]. A new kind of line is added to LINE_KINDS and nowhere else.
  var LINE_KINDS = [
    {
      name: "styling",
      read: function (s) {
        return STYLING.test(s) ? { nodes: [], links: [] } : null;
      },
    },
    {
      name: "lines this pen does not draw",
      read: function (s) {
        if (/^%%/.test(s)) throw new Error('A "%%" comment is not part of a flow this pen draws; remove the line.');
        if (/^(subgraph|end)$/.test(s) || /^subgraph[ \t]/.test(s)) {
          throw new Error('"' + s.split(/\s/)[0] + '" is not drawn; a flow has no groups.');
        }
        if (/^click[ \t]/.test(s)) throw new Error('"click" lines are not drawn; a flow has no links.');
        if (/^direction[ \t]/.test(s)) throw new Error('"direction" lines are not drawn; the header sets the direction.');
        return null;
      },
    },
    {
      name: "steps and arrows",
      read: function (s) {
        var nodes = [];
        var links = [];
        var pos = 0;
        for (;;) {
          var step = readStep(s, pos);
          nodes.push(step.node);
          pos = skipSpaces(s, step.end);
          if (pos >= s.length) break;
          if (s[pos] === "&") throw new Error('Steps joined with "&" are not drawn; write one arrow per pair.');
          var arrow = readArrow(s, pos);
          links.push({ dashed: arrow.dashed, label: arrow.label });
          pos = skipSpaces(s, arrow.end);
          if (pos >= s.length) throw new Error("An arrow needs a step after it.");
        }
        return { nodes: nodes, links: links };
      },
    },
  ];

  function readLineKinds(text) {
    for (var i = 0; i < LINE_KINDS.length; i++) {
      var got = LINE_KINDS[i].read(text);
      if (got) return got;
    }
    return null;
  }

  function clean(raw) {
    var text = raw.trim();
    if (text.charAt(text.length - 1) === ";") text = text.slice(0, -1).trim();
    return text;
  }

  function headerReason(text) {
    var direction = /^(?:flowchart|graph)[ \t]+(RL|BT)$/.exec(text);
    if (direction) return '"' + direction[1] + '" is not a direction this pen draws; use LR, TD or TB.';
    return 'The first line must be a header: "flowchart LR", "flowchart TD" or "flowchart TB".';
  }

  function declare(flow, node) {
    var step = flow.steps.get(node.id);
    if (!step) {
      step = {
        id: node.id,
        shape: node.shape || "box",
        label: node.label === null ? node.id : node.label,
        labelled: node.label !== null,
        accent: false,
      };
      flow.steps.set(node.id, step);
      flow.order.push(node.id);
    } else if (node.label !== null) {
      if (!step.labelled) {
        step.shape = node.shape;
        step.label = node.label;
        step.labelled = true;
      } else if (step.shape !== node.shape || step.label !== node.label) {
        throw new Error('Step "' + node.id + '" is already drawn with a different shape or label; a step has one of each.');
      }
    }
    if (node.accent) step.accent = true;
  }

  function reaches(adjacency, from, to) {
    var seen = new Set([from]);
    var stack = [from];
    while (stack.length) {
      var at = stack.pop();
      if (at === to) return true;
      var next = adjacency.get(at) || [];
      for (var i = 0; i < next.length; i++) {
        if (!seen.has(next[i])) {
          seen.add(next[i]);
          stack.push(next[i]);
        }
      }
    }
    return false;
  }

  // Arrows in written order; every one whose target already reaches its source would close a loop, and
  // is marked as a loop back. Each check walks the arrows kept so far once, never one path at a time.
  function findLoops(arrows) {
    var adjacency = new Map();
    var loops = [];
    for (var i = 0; i < arrows.length; i++) {
      var arrow = arrows[i];
      arrow.loop = arrow.from === arrow.to || reaches(adjacency, arrow.to, arrow.from);
      if (arrow.loop) {
        loops.push(arrow);
        continue;
      }
      if (!adjacency.has(arrow.from)) adjacency.set(arrow.from, []);
      adjacency.get(arrow.from).push(arrow.to);
    }
    return loops;
  }

  // The limits that need no parsing, in the order they are checked.
  function checkSize(lines) {
    if (lines.length > MAX_LINES) {
      throw wholeFlow("The flow has " + lines.length + " lines; at most " + MAX_LINES + " are drawn.");
    }
    for (var i = 0; i < lines.length; i++) {
      if (lines[i].length > MAX_LINE_LENGTH) {
        throw wholeFlow("Line " + (i + 1) + " has " + lines[i].length + " characters; at most " + MAX_LINE_LENGTH + " are drawn.");
      }
    }
  }

  function parse(text) {
    var lines = String(text).split(/\r\n|\r|\n/);
    checkSize(lines);
    var flow = { direction: null, steps: new Map(), order: [], arrows: [] };
    var problems = [];
    var texts = new Map();
    function problem(lineNo, message) {
      problems.push({ line: lineNo, text: texts.get(lineNo), message: message });
    }
    for (var i = 0; i < lines.length; i++) {
      var lineNo = i + 1;
      var cleaned = clean(lines[i]);
      if (cleaned === "") continue;
      texts.set(lineNo, cleaned);
      if (flow.direction === null) {
        var header = HEADER.exec(cleaned);
        if (!header) problem(lineNo, headerReason(cleaned));
        flow.direction = header && header[1] !== "LR" ? "TD" : "LR";
        continue;
      }
      try {
        var got = readLineKinds(cleaned);
        if (!got) throw new Error("This pen does not draw this line.");
        got.nodes.forEach(function (node, k) {
          declare(flow, node);
          if (k > 0) {
            var link = got.links[k - 1];
            flow.arrows.push({
              index: flow.arrows.length,
              from: got.nodes[k - 1].id,
              to: node.id,
              dashed: link.dashed,
              label: link.label,
            });
          }
        });
      } catch (error) {
        problem(lineNo, error.message);
      }
    }
    if (flow.order.length > MAX_STEPS) {
      throw wholeFlow("The flow has " + flow.order.length + " steps; at most " + MAX_STEPS + " are drawn.");
    }
    if (flow.arrows.length > MAX_ARROWS) {
      throw wholeFlow("The flow has " + flow.arrows.length + " arrows; at most " + MAX_ARROWS + " are drawn.");
    }
    if (problems.length) {
      problems.sort(function (a, b) { return a.line - b.line; });
      throw failed(problems);
    }
    if (flow.order.length === 0) throw wholeFlow("The flow has no steps.");
    findLoops(flow.arrows);
    var layer = layersOf(flow);
    var waypoints = 0;
    flow.arrows.forEach(function (arrow) {
      if (arrow.loop) return;
      waypoints += layer.get(arrow.to) - layer.get(arrow.from) - 1;
    });
    if (waypoints > MAX_WAYPOINTS) {
      throw wholeFlow("The flow's arrows pass " + waypoints + " waypoints; at most " + MAX_WAYPOINTS + " are drawn.");
    }
    return flow;
  }

  // Breaks a word that is wider than max into the fewest lines of the longest prefixes that fit,
  // finding each break by bisection so the page measures a handful of candidates, not every prefix.
  function breakWord(word, measure, max) {
    var chars = Array.from(word);
    var parts = [];
    var start = 0;
    while (start < chars.length) {
      var lo = 1;
      var hi = chars.length - start;
      while (lo < hi) {
        var mid = Math.ceil((lo + hi) / 2);
        if (measure(chars.slice(start, start + mid).join("")) > max) hi = mid - 1;
        else lo = mid;
      }
      parts.push(chars.slice(start, start + lo).join(""));
      start += lo;
    }
    return parts;
  }

  function wrap(label, measure, max) {
    var words = label.split(/\s+/).filter(function (w) { return w !== ""; });
    if (words.length === 0) return [""];
    var lines = [];
    var line = "";
    words.forEach(function (word) {
      var pieces = measure(word) > max ? breakWord(word, measure, max) : [word];
      pieces.forEach(function (piece, k) {
        var joined = line === "" ? piece : line + " " + piece;
        if (line !== "" && (k > 0 || measure(joined) > max)) {
          lines.push(line);
          line = piece;
        } else {
          line = joined;
        }
      });
    });
    lines.push(line);
    return lines;
  }

  // A label wrapped to the widest line allowed: its lines, and the width and height they take.
  function textBlock(text, measure) {
    var lines = wrap(text, measure, MAX_LABEL_WIDTH);
    return {
      lines: lines,
      w: Math.max.apply(null, lines.map(function (l) { return measure(l); })),
      h: lines.length * LINE_HEIGHT,
    };
  }

  function sized(step, measure) {
    var block = textBlock(step.label, measure);
    var lines = block.lines;
    var tw = block.w;
    var th = block.h;
    var w;
    var h;
    if (step.shape === "decision") {
      w = 2 * (tw + 2 * PAD_X);
      h = 2 * (th + 2 * PAD_Y);
    } else {
      h = th + 2 * PAD_Y;
      w = tw + 2 * PAD_X + (step.shape === "pill" ? h / 2 : 0);
    }
    return { lines: lines, w: w, h: h };
  }

  // Longest chain of forward arrows leading into each step; loop backs are set aside.
  function layersOf(flow) {
    var into = new Map();
    var outOf = new Map();
    flow.order.forEach(function (id) {
      into.set(id, 0);
      outOf.set(id, []);
    });
    flow.arrows.forEach(function (arrow) {
      if (arrow.loop) return;
      into.set(arrow.to, into.get(arrow.to) + 1);
      outOf.get(arrow.from).push(arrow.to);
    });
    var layer = new Map();
    var queue = flow.order.filter(function (id) { return into.get(id) === 0; });
    queue.forEach(function (id) { layer.set(id, 0); });
    for (var q = 0; q < queue.length; q++) {
      var id = queue[q];
      outOf.get(id).forEach(function (next) {
        layer.set(next, Math.max(layer.get(next) || 0, layer.get(id) + 1));
        into.set(next, into.get(next) - 1);
        if (into.get(next) === 0) queue.push(next);
      });
    }
    return layer;
  }

  function mean(list, position) {
    var sum = 0;
    list.forEach(function (n) { sum += position.get(n); });
    return sum / list.length;
  }

  function sweep(layers, nodes, from, to, neighbours) {
    var step = from < to ? 1 : -1;
    for (var l = from; l !== to; l += step) {
      var against = layers[l - step];
      var position = new Map();
      against.forEach(function (n, k) { position.set(n, k); });
      var current = new Map();
      layers[l].forEach(function (n, k) { current.set(n, k); });
      var key = new Map();
      layers[l].forEach(function (n) {
        var near = neighbours(nodes[n]).filter(function (m) { return position.has(m); });
        key.set(n, near.length ? mean(near, position) : current.get(n));
      });
      layers[l].sort(function (a, b) { return key.get(a) - key.get(b) || nodes[a].appear - nodes[b].appear; });
    }
  }

  // The label sits at the middle of the arrow's first leg, set on a halo that hides the line behind it.
  function labelAt(block, from, to) {
    var mx = (from[0] + to[0]) / 2;
    var my = (from[1] + to[1]) / 2;
    var lines = block.lines;
    var tw = block.w;
    var th = block.h;
    return {
      lines: lines.map(function (line, k) {
        return { text: line, x: mx, y: my + (k - (lines.length - 1) / 2) * LINE_HEIGHT };
      }),
      halo: { x: mx - tw / 2 - HALO_PAD_X, y: my - th / 2 - HALO_PAD_Y, w: tw + 2 * HALO_PAD_X, h: th + 2 * HALO_PAD_Y },
    };
  }

  function layout(flow, measure) {
    var LR = flow.direction === "LR";
    var layer = layersOf(flow);
    var nodes = [];
    var nodeOfStep = new Map();
    flow.order.forEach(function (id) {
      var step = flow.steps.get(id);
      var size = sized(step, measure);
      nodeOfStep.set(id, nodes.length);
      nodes.push({ step: step, layer: layer.get(id), size: size, preds: [], succs: [], appear: nodes.length });
    });
    var forward = flow.arrows.filter(function (arrow) { return !arrow.loop; });
    var loops = flow.arrows.filter(function (arrow) { return arrow.loop; });
    var routes = forward.map(function (arrow) {
      var route = [nodeOfStep.get(arrow.from)];
      for (var l = layer.get(arrow.from) + 1; l < layer.get(arrow.to); l++) {
        route.push(nodes.length);
        nodes.push({ step: null, layer: l, size: { w: LR ? 0 : LANE, h: LR ? LANE : 0 }, preds: [], succs: [], appear: nodes.length });
      }
      route.push(nodeOfStep.get(arrow.to));
      return route;
    });
    routes.forEach(function (route) {
      for (var k = 0; k + 1 < route.length; k++) {
        nodes[route[k]].succs.push(route[k + 1]);
        nodes[route[k + 1]].preds.push(route[k]);
      }
    });

    var depth = 0;
    nodes.forEach(function (n) { depth = Math.max(depth, n.layer); });
    var layers = [];
    for (var l = 0; l <= depth; l++) layers.push([]);
    nodes.forEach(function (n, i) { layers[n.layer].push(i); });
    sweep(layers, nodes, 1, depth + 1, function (n) { return n.preds; });
    if (depth >= 2) sweep(layers, nodes, depth - 1, 0, function (n) { return n.succs; });
    sweep(layers, nodes, 1, depth + 1, function (n) { return n.preds; });

    function main(n) { return LR ? n.size.w : n.size.h; }
    function cross(n) { return LR ? n.size.h : n.size.w; }

    var columns = layers.map(function (members) {
      return Math.max.apply(null, [0].concat(members.map(function (i) { return main(nodes[i]); })));
    });
    var start = [];
    var at = MARGIN;
    columns.forEach(function (width, l) {
      start.push(at);
      at += width + GAP_MAIN;
    });
    var mainExtent = at - GAP_MAIN + MARGIN;
    var totals = layers.map(function (members) {
      return members.reduce(function (sum, i) { return sum + cross(nodes[i]); }, 0) + GAP_CROSS * (members.length - 1);
    });
    var crossExtent = Math.max.apply(null, totals) + 2 * MARGIN;
    layers.forEach(function (members, l) {
      var offset = MARGIN + (crossExtent - 2 * MARGIN - totals[l]) / 2;
      members.forEach(function (i) {
        var n = nodes[i];
        n.mainStart = start[l] + (columns[l] - main(n)) / 2;
        n.crossStart = offset;
        n.crossMid = offset + cross(n) / 2;
        offset += cross(n) + GAP_CROSS;
      });
    });

    function at2(m, c) { return LR ? [m, c] : [c, m]; }
    var colRight = layers.map(function (members, l) { return start[l] + columns[l]; });

    var plan = {
      width: LR ? mainExtent : crossExtent,
      height: LR ? crossExtent : mainExtent,
      steps: [],
      arrows: [],
    };
    flow.order.forEach(function (id) {
      var n = nodes[nodeOfStep.get(id)];
      var topLeft = at2(n.mainStart, n.crossStart);
      var lines = n.size.lines.map(function (text, k) {
        return { text: text, x: topLeft[0] + n.size.w / 2, y: topLeft[1] + n.size.h / 2 + (k - (n.size.lines.length - 1) / 2) * LINE_HEIGHT };
      });
      plan.steps.push({ id: id, shape: n.step.shape, x: topLeft[0], y: topLeft[1], w: n.size.w, h: n.size.h, accent: n.step.accent, lines: lines });
    });
    forward.forEach(function (arrow, a) {
      var route = routes[a];
      var source = nodes[route[0]];
      var points = [at2(source.mainStart + main(source), source.crossMid)];
      if (colRight[source.layer] > source.mainStart + main(source)) points.push(at2(colRight[source.layer], source.crossMid));
      for (var k = 1; k < route.length; k++) {
        var n = nodes[route[k]];
        points.push(at2(start[n.layer], n.crossMid));
        if (k < route.length - 1) {
          if (colRight[n.layer] > start[n.layer]) points.push(at2(colRight[n.layer], n.crossMid));
        } else if (n.mainStart > start[n.layer]) {
          points.push(at2(n.mainStart, n.crossMid));
        }
      }
      plan.arrows.push({
        index: arrow.index,
        from: arrow.from,
        to: arrow.to,
        dashed: arrow.dashed,
        loop: false,
        points: points,
        label: arrow.label === null ? null : labelAt(textBlock(arrow.label, measure), points[0], points[1]),
      });
    });

    // Loop backs. Abstract coordinates (m along the flow, c across it); the lane side is +c.
    var gapUsed = layers.map(function () { return 0; });
    var cursor = crossExtent - MARGIN + LANE;
    var stepNodes = flow.order.map(function (id) { return nodes[nodeOfStep.get(id)]; });

    // Where a step's lane-side edge lies at m, measured across the flow.
    function edgeAt(n, m) {
      if (n.step.shape !== "decision") return n.crossStart + cross(n);
      var half = main(n) / 2;
      return n.crossMid + (cross(n) / 2) * (1 - Math.abs(m - (n.mainStart + half)) / half);
    }

    // A step beyond n on the lane side, over the same stretch of the flow, would be crossed by a straight drop.
    function blocked(n, m) {
      return stepNodes.some(function (o) {
        return o !== n && o.layer === n.layer && o.crossStart > n.crossStart && o.mainStart - 2 < m && m < o.mainStart + main(o) + 2;
      });
    }

    // The way out of (or into) a step's lane-side edge to the lane: a straight run when nothing lies in the
    // way, otherwise through the gap beneath the step and down the gap beside its layer. row is the
    // fraction of the gap beneath the step where the sideways run goes.
    function reach(n, m, laneC, row) {
      var e = edgeAt(n, m);
      var out = [[m, e]];
      if (blocked(n, m)) {
        var x = colRight[n.layer] + 2 + (gapUsed[n.layer]++ % 4) * 3;
        var y = n.crossStart + cross(n) + GAP_CROSS * row;
        out.push([m, y], [x, y], [x, laneC]);
      } else {
        out.push([m, laneC]);
      }
      return out;
    }

    var loopLabels = loops.map(function (arrow) { return arrow.label === null ? null : textBlock(arrow.label, measure); });
    var lanes = loopLabels.map(function (block) {
      var band = LANE + (block ? block.h + 2 * HALO_PAD_Y : 0);
      var laneC = cursor + band / 2;
      cursor += band;
      return laneC;
    });
    // Where each loop back meets its steps along the lane-side edge. A step with one loop back keeps it at
    // the middle (a self-loop's two ends a quarter either side); a step with several shares its edge, so
    // every end takes its own evenly spaced point, in written order, and no two arrowheads or legs coincide.
    var ends = new Map();
    function endsOn(n) {
      if (!ends.has(n)) ends.set(n, []);
      return ends.get(n);
    }
    loops.forEach(function (arrow, k) {
      endsOn(nodes[nodeOfStep.get(arrow.from)]).push({ k: k, role: "from" });
      endsOn(nodes[nodeOfStep.get(arrow.to)]).push({ k: k, role: "to" });
    });
    function endAt(n, k, role) {
      var list = endsOn(n);
      var loneArrow = list.every(function (e) { return e.k === list[0].k; });
      var mid = n.mainStart + main(n) / 2;
      if (loneArrow) {
        if (list.length === 1) return mid;
        return role === "from" ? mid - main(n) / 4 : mid + main(n) / 4;
      }
      var i = list.findIndex(function (e) { return e.k === k && e.role === role; });
      return n.mainStart + (main(n) * (i + 1)) / (list.length + 1);
    }
    loops.forEach(function (arrow, k) {
      var from = nodes[nodeOfStep.get(arrow.from)];
      var to = nodes[nodeOfStep.get(arrow.to)];
      var mFrom = endAt(from, k, "from");
      var mTo = endAt(to, k, "to");
      var entry = reach(to, mTo, lanes[k], 2 / 3);
      var exit = reach(from, mFrom, lanes[k], 1 / 3);
      var laneRun = [exit[exit.length - 1], entry[entry.length - 1]];
      var route = exit.concat(entry.reverse());
      var points = route.map(function (p) { return at2(p[0], p[1]); });
      plan.arrows.push({
        index: arrow.index,
        from: arrow.from,
        to: arrow.to,
        dashed: arrow.dashed,
        loop: true,
        points: points,
        label: loopLabels[k] && labelAt(loopLabels[k], at2(laneRun[0][0], laneRun[0][1]), at2(laneRun[1][0], laneRun[1][1])),
      });
    });
    if (loops.length) {
      var grown = cursor + MARGIN;
      if (LR) plan.height = grown;
      else plan.width = grown;
    }
    return plan;
  }

  window.PadFlow = { parse: parse, layout: layout };
})();

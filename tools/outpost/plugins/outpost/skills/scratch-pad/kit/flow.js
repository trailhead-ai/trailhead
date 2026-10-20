/*
 * The flow contract: parse a flow written in a small part of Mermaid's flowchart syntax, and lay it
 * out in layers. No drawing happens here; pen.js traces what layout() returns.
 *
 *   PadFlow.parse(text)            -> flow, or throws a FlowError
 *   PadFlow.layout(flow, measure)  -> drawing plan
 *
 * flow = {
 *   direction: "LR" | "TD",                       // TB is read as TD
 *   steps:  Map id -> { id, shape, label, labelled, index }   // shape: box | round | pill | decision
 *   order:  [id, ...]                             // steps in order of first appearance
 *   arrows: [{ index, from, to, line }]           // index is the written order; ids, not step objects
 * }
 *
 * plan = {
 *   width, height,                                // the flow's natural size, in SVG units
 *   steps:  [{ id, shape, x, y, w, h, lines: [{ text, x, y }] }]   // x, y: top-left; lines: label centres
 *   arrows: [{ index, from, to, points: [[x, y], ...] }]           // from the source edge to the target edge
 * }
 *
 * measure(text) returns the width of one line of label text as the browser sets it. Parse and layout
 * are pure functions of the flow text and measure(); nothing depends on the page width. Every table
 * keyed by a step id is a Map, so an id such as __proto__ is an ordinary step.
 *
 * A line the flow does not understand throws a FlowError carrying line (1-based, or null for the whole
 * flow), text (the line as written) and message (what is wrong).
 */
(function () {
  "use strict";

  var MAX_TEXT = 200;
  var MAX_LABEL_WIDTH = 200;
  var LINE_HEIGHT = 21;
  var PAD_X = 14;
  var PAD_Y = 10;
  var GAP_MAIN = 64;
  var GAP_CROSS = 28;
  var LANE = 18;
  var MARGIN = 14;

  function FlowError(line, text, message) {
    var error = new Error(message);
    error.name = "FlowError";
    error.isFlowError = true;
    error.flowLine = line;
    error.flowText = text === null ? null : text.length > MAX_TEXT ? text.slice(0, MAX_TEXT) + "…" : text;
    return error;
  }

  var HEADER = /^(?:flowchart|graph)[ \t]+(LR|TD|TB)$/;
  var ID = /[A-Za-z0-9_]+/y;
  var SHAPES = [
    { open: "([", close: "])", shape: "pill" },
    { open: "(", close: ")", shape: "round" },
    { open: "[", close: "]", shape: "box" },
    { open: "{", close: "}", shape: "decision" },
  ];
  var LABEL_FORBIDDEN = /[\[\](){}"|]/;

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
    var node = { id: m[0], shape: null, label: null };
    pos += m[0].length;
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
    return { node: node, end: pos };
  }

  // A line kind turns one cleaned line into what it contributes, or returns null when the line is
  // not its kind. A new kind of line is added to LINE_KINDS and nowhere else.
  var LINE_KINDS = [
    {
      name: "steps and arrows",
      read: function (s) {
        var nodes = [];
        var pos = 0;
        for (;;) {
          var step = readStep(s, pos);
          nodes.push(step.node);
          pos = skipSpaces(s, step.end);
          if (pos >= s.length) break;
          if (s.substr(pos, 3) !== "-->") {
            throw new Error('Expected "-->" or the end of the line after a step; this pen draws plain arrows only.');
          }
          pos = skipSpaces(s, pos + 3);
        }
        return { nodes: nodes };
      },
    },
  ];

  function explain(text, error) {
    if (/^(subgraph|end)\b/.test(text)) return '"' + text.split(/\s/)[0] + '" is not drawn; a flow has no groups.';
    return error.message;
  }

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

  function declare(flow, node, lineNo, text) {
    var step = flow.steps.get(node.id);
    if (!step) {
      step = { id: node.id, shape: node.shape || "box", label: node.label === null ? node.id : node.label, labelled: node.label !== null, index: flow.order.length };
      flow.steps.set(node.id, step);
      flow.order.push(node.id);
      return;
    }
    if (node.label === null) return;
    if (!step.labelled) {
      step.shape = node.shape;
      step.label = node.label;
      step.labelled = true;
    } else if (step.shape !== node.shape || step.label !== node.label) {
      throw FlowError(lineNo, text, 'Step "' + node.id + '" is already drawn with a different shape or label; a step has one of each.');
    }
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

  // Arrows in written order; one whose target already reaches its source would close a loop.
  function findLoop(arrows) {
    var adjacency = new Map();
    for (var i = 0; i < arrows.length; i++) {
      var arrow = arrows[i];
      if (arrow.from === arrow.to || reaches(adjacency, arrow.to, arrow.from)) return arrow;
      if (!adjacency.has(arrow.from)) adjacency.set(arrow.from, []);
      adjacency.get(arrow.from).push(arrow.to);
    }
    return null;
  }

  function parse(text) {
    var lines = String(text).split(/\r\n|\r|\n/);
    var flow = { direction: null, steps: new Map(), order: [], arrows: [] };
    var texts = new Map();
    for (var i = 0; i < lines.length; i++) {
      var lineNo = i + 1;
      var cleaned = clean(lines[i]);
      if (cleaned === "") continue;
      if (flow.direction === null) {
        var header = HEADER.exec(cleaned);
        if (!header) {
          throw FlowError(lineNo, cleaned, 'The first line must be a header: "flowchart LR", "flowchart TD" or "flowchart TB".');
        }
        flow.direction = header[1] === "LR" ? "LR" : "TD";
        continue;
      }
      var got;
      try {
        got = readLineKinds(cleaned);
      } catch (error) {
        throw FlowError(lineNo, cleaned, explain(cleaned, error));
      }
      if (!got) throw FlowError(lineNo, cleaned, explain(cleaned, new Error("This pen does not draw this line.")));
      got.nodes.forEach(function (node, k) {
        declare(flow, node, lineNo, cleaned);
        if (k > 0) {
          flow.arrows.push({ index: flow.arrows.length, from: got.nodes[k - 1].id, to: node.id, line: lineNo });
        }
      });
      texts.set(lineNo, cleaned);
    }
    if (flow.order.length === 0) throw FlowError(null, null, "The flow has no steps.");
    var loop = findLoop(flow.arrows);
    if (loop) throw FlowError(loop.line, texts.get(loop.line), "This arrow closes a loop, and loops are not drawn yet.");
    return flow;
  }

  function breakWord(word, measure, max) {
    var parts = [];
    var chunk = "";
    Array.from(word).forEach(function (ch) {
      if (chunk !== "" && measure(chunk + ch) > max) {
        parts.push(chunk);
        chunk = "";
      }
      chunk += ch;
    });
    if (chunk !== "") parts.push(chunk);
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

  function sized(step, measure) {
    var lines = wrap(step.label, measure, MAX_LABEL_WIDTH);
    var tw = Math.max.apply(null, lines.map(function (l) { return measure(l); }));
    var th = lines.length * LINE_HEIGHT;
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

  // Longest chain of arrows leading into each step; every arrow in a loop-free flow points forward.
  function layersOf(flow) {
    var into = new Map();
    var outOf = new Map();
    flow.order.forEach(function (id) {
      into.set(id, 0);
      outOf.set(id, []);
    });
    flow.arrows.forEach(function (arrow) {
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
    var routes = flow.arrows.map(function (arrow) {
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
      plan.steps.push({ id: id, shape: n.step.shape, x: topLeft[0], y: topLeft[1], w: n.size.w, h: n.size.h, lines: lines });
    });
    flow.arrows.forEach(function (arrow, a) {
      var route = routes[a];
      var source = nodes[route[0]];
      var points = [at2(source.mainStart + main(source), source.crossMid)];
      var colEnd = start[source.layer] + columns[source.layer];
      if (colEnd > source.mainStart + main(source)) points.push(at2(colEnd, source.crossMid));
      for (var k = 1; k < route.length; k++) {
        var n = nodes[route[k]];
        points.push(at2(start[n.layer], n.crossMid));
        if (k < route.length - 1) {
          var colRight = start[n.layer] + columns[n.layer];
          if (colRight > start[n.layer]) points.push(at2(colRight, n.crossMid));
        } else if (n.mainStart > start[n.layer]) {
          points.push(at2(n.mainStart, n.crossMid));
        }
      }
      plan.arrows.push({ index: arrow.index, from: arrow.from, to: arrow.to, points: points });
    });
    return plan;
  }

  window.PadFlow = { parse: parse, layout: layout, FlowError: FlowError };
})();

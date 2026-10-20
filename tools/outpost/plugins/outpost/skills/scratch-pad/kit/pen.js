(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var MAX_DEPTH = 24;
  var WOBBLE = 0.9;
  var SEGMENT = 22;
  var MAX_QUOTE = 200;
  var MAX_LISTED = 5;
  var MIN_SCALE = 0.6;
  var REDRAW = "Ask the session to redraw this option.";
  var ARROW_LENGTH = 11;
  var HATCH_GAP = 8;
  var HALO_PAD = 2;
  var ARROW_SPREAD = (26 * Math.PI) / 180;

  function mulberry32(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function seedFor(qIndex, oIndex) {
    return (qIndex + 1) * 7919 + (oIndex + 1) * 104729;
  }

  function svgNode(tag) {
    return document.createElementNS(SVG_NS, tag);
  }

  function round1(n) {
    return Math.round(n * 10) / 10;
  }

  function densify(base) {
    var out = [];
    for (var i = 0; i < base.length - 1; i++) {
      var a = base[i];
      var b = base[i + 1];
      var len = Math.sqrt(Math.pow(b[0] - a[0], 2) + Math.pow(b[1] - a[1], 2));
      var n = Math.max(1, Math.ceil(len / SEGMENT));
      for (var k = 0; k < n; k++) {
        out.push([a[0] + ((b[0] - a[0]) * k) / n, a[1] + ((b[1] - a[1]) * k) / n]);
      }
    }
    out.push(base[base.length - 1]);
    return out;
  }

  function stroke(group, rng, base, cls) {
    for (var pass = 0; pass < 2; pass++) {
      var d = densify(base)
        .map(function (p, i) {
          var x = p[0] + (rng() * 2 - 1) * WOBBLE;
          var y = p[1] + (rng() * 2 - 1) * WOBBLE;
          return (i === 0 ? "M" : "L") + round1(x) + " " + round1(y);
        })
        .join(" ");
      var path = svgNode("path");
      path.setAttribute("d", d);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", "currentColor");
      path.setAttribute("stroke-width", "1.5");
      path.setAttribute("stroke-linecap", "round");
      path.setAttribute("stroke-linejoin", "round");
      if (cls) path.setAttribute("class", cls);
      group.appendChild(path);
    }
  }

  function roundedOutline(r, radius) {
    var x = r.x;
    var y = r.y;
    var w = r.w;
    var h = r.h;
    var c = Math.max(0, Math.min(radius, w / 2, h / 2));
    if (c === 0) return [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]];
    var pts = [[x + c, y]];
    var corners = [
      [x + w - c, y + c, -90],
      [x + w - c, y + h - c, 0],
      [x + c, y + h - c, 90],
      [x + c, y + c, 180],
    ];
    corners.forEach(function (corner) {
      for (var s = 0; s <= 6; s++) {
        var angle = ((corner[2] + (90 * s) / 6) * Math.PI) / 180;
        pts.push([corner[0] + c * Math.cos(angle), corner[1] + c * Math.sin(angle)]);
      }
    });
    pts.push([x + c, y]);
    return pts;
  }

  function traceBox(group, rng, r) {
    stroke(group, rng, roundedOutline(r, 0));
  }

  function traceButton(group, rng, r) {
    stroke(group, rng, roundedOutline(r, 12));
  }

  function tracePill(group, rng, r) {
    stroke(group, rng, roundedOutline(r, r.h / 2));
  }

  function traceImage(group, rng, r) {
    stroke(group, rng, roundedOutline(r, 0));
    stroke(group, rng, [[r.x, r.y], [r.x + r.w, r.y + r.h]]);
    stroke(group, rng, [[r.x + r.w, r.y], [r.x, r.y + r.h]]);
  }

  function traceRule(group, rng, r) {
    var y = r.y + r.h / 2;
    stroke(group, rng, [[r.x, y], [r.x + r.w, y]]);
  }

  var KINDS = new Map([
    ["box", { name: "box", value: "list", cls: "pen-box", trace: traceBox }],
    ["row", { name: "row", value: "list", cls: "pen-row" }],
    ["column", { name: "column", value: "list", cls: "pen-column" }],
    ["text", { name: "text", value: "string", cls: "pen-text" }],
    ["button", { name: "button", value: "string", cls: "pen-button", trace: traceButton }],
    ["pill", { name: "pill", value: "string", cls: "pen-pill", trace: tracePill }],
    ["field", { name: "field", value: "string", cls: "pen-field", trace: traceBox }],
    ["image", { name: "image", value: "string", cls: "pen-image", trace: traceImage }],
    ["rule", { name: "rule", value: "true", cls: "pen-rule", trace: traceRule }],
  ]);

  var VALUE_NEEDS = {
    list: "a list of blocks",
    string: "a string",
    true: "true",
  };

  function quote(v, depth) {
    if (v === undefined) return "undefined";
    if (v === null || typeof v !== "object") {
      var s = JSON.stringify(v);
      return s === undefined ? String(v) : s;
    }
    if (depth >= 3) return Array.isArray(v) ? "[…]" : "{…}";
    if (Array.isArray(v)) {
      return "[" + v.map(function (x) { return quote(x, depth + 1); }).join(", ") + "]";
    }
    return (
      "{" +
      Object.keys(v)
        .map(function (k) { return JSON.stringify(k) + ": " + quote(v[k], depth + 1); })
        .join(", ") +
      "}"
    );
  }

  function quoted(v) {
    var text;
    try {
      text = quote(v, 0);
    } catch (error) {
      return "(a block that cannot be printed)";
    }
    return text.length > MAX_QUOTE ? text.slice(0, MAX_QUOTE) + "…" : text;
  }

  function unreadable(path, block, reason) {
    var error = new Error(reason);
    error.penPath = path.slice();
    error.penBlock = block;
    error.hasBlock = true;
    return error;
  }

  function validate(block, path) {
    if (block === null || typeof block !== "object" || Array.isArray(block)) {
      throw unreadable(path, block, "A block is an object with exactly one key.");
    }
    var keys = Object.keys(block);
    if (keys.length !== 1) {
      throw unreadable(
        path,
        block,
        "A block needs exactly one key; this one has " + (keys.length === 0 ? "none" : keys.length) + "."
      );
    }
    var key = keys[0];
    var kind = KINDS.get(key);
    if (!kind) throw unreadable(path, block, JSON.stringify(key) + " is not a block kind.");
    var value = block[key];
    var ok =
      kind.value === "list" ? Array.isArray(value) : kind.value === "string" ? typeof value === "string" : value === true;
    if (!ok) throw unreadable(path, block, JSON.stringify(kind.name) + " needs " + VALUE_NEEDS[kind.value] + ".");
    return { kind: kind, value: value };
  }

  function buildBlock(block, path, ctx) {
    if (path.length >= MAX_DEPTH * 2) {
      throw unreadable(path, block, "Blocks are nested too deeply to draw.");
    }
    var parsed = validate(block, path);
    var kind = parsed.kind;
    var el = document.createElement("div");
    el.className = "pen-block " + kind.cls;
    var item = { index: ctx.items.length, kind: kind, el: el, target: el };
    el.setAttribute("data-pen-index", String(item.index));
    ctx.items.push(item);
    if (kind.value === "list") {
      parsed.value.forEach(function (child, i) {
        el.appendChild(buildBlock(child, path.concat([kind.name, i]), ctx));
      });
    } else if (kind.value === "string") {
      var label = document.createElement("span");
      label.className = "pen-label";
      label.textContent = parsed.value;
      el.appendChild(label);
      if (kind.name === "field") {
        var input = document.createElement("div");
        input.className = "pen-input";
        el.appendChild(input);
        item.target = input;
      }
    }
    return el;
  }

  function showMessage(state, error) {
    state.dead = true;
    if (state.observer) state.observer.disconnect();
    var lines;
    if (error && error.isFlowError) {
      lines = ["Can't draw this flow:"];
      error.problems.slice(0, MAX_LISTED).forEach(function (problem) {
        lines.push(problem.line === null ? "the whole flow" : "line " + problem.line + ": " + problem.text);
        lines.push(problem.message);
      });
      if (error.problems.length > MAX_LISTED) lines.push("and " + (error.problems.length - MAX_LISTED) + " more");
      lines.push(REDRAW);
    } else {
      var segments;
      var reason;
      if (error && error.hasBlock) {
        segments = error.penPath.slice();
        segments.push(quoted(error.penBlock));
        reason = error.message;
      } else {
        segments = [quoted(state.layout)];
        reason = String((error && error.message) || error);
      }
      if (segments.length > 7) segments = ["…"].concat(segments.slice(-5));
      lines = ["Can't draw this " + (state.flow ? "flow" : "layout") + ":", segments.join(" › "), reason];
      if (state.flow) lines.push(REDRAW);
    }
    var box = document.createElement("div");
    box.className = "pen-message";
    lines.forEach(function (line) {
      var p = document.createElement("p");
      p.textContent = line;
      box.appendChild(p);
    });
    state.drawing.textContent = "";
    state.drawing.appendChild(box);
  }

  function trace(state) {
    if (state.flow) {
      traceFlow(state);
      return;
    }
    var base = state.svg.getBoundingClientRect();
    var rng = mulberry32(state.seed);
    state.svg.textContent = "";
    state.items.forEach(function (item) {
      if (!item.kind.trace) return;
      var r = item.target.getBoundingClientRect();
      var group = svgNode("g");
      group.setAttribute("data-pen-index", String(item.index));
      group.setAttribute("data-kind", item.kind.name);
      item.kind.trace(group, rng, { x: r.left - base.left, y: r.top - base.top, w: r.width, h: r.height });
      state.svg.appendChild(group);
    });
  }

  function guarded(state, fn) {
    if (state.dead) return;
    try {
      fn();
    } catch (error) {
      showMessage(state, error);
    }
  }

  function stepOutline(step) {
    var r = { x: step.x, y: step.y, w: step.w, h: step.h };
    if (step.shape === "round") return roundedOutline(r, 12);
    if (step.shape === "pill") return roundedOutline(r, r.h / 2);
    if (step.shape === "decision") {
      var cx = r.x + r.w / 2;
      var cy = r.y + r.h / 2;
      return [[cx, r.y], [r.x + r.w, cy], [cx, r.y + r.h], [r.x, cy], [cx, r.y]];
    }
    return roundedOutline(r, 0);
  }

  // The part of the line through p with direction (dx, dy) that lies inside a convex polygon, or null.
  function clipLine(poly, p, dx, dy) {
    var lo = -Infinity;
    var hi = Infinity;
    var area = 0;
    for (var i = 0; i < poly.length - 1; i++) area += poly[i][0] * poly[i + 1][1] - poly[i + 1][0] * poly[i][1];
    var turn = area >= 0 ? 1 : -1;
    for (var k = 0; k < poly.length - 1; k++) {
      var a = poly[k];
      var b = poly[k + 1];
      var ex = b[0] - a[0];
      var ey = b[1] - a[1];
      if (ex === 0 && ey === 0) continue;
      var inward = turn * (ex * (p[1] - a[1]) - ey * (p[0] - a[0]));
      var rate = turn * (ex * dy - ey * dx);
      if (Math.abs(rate) < 1e-9) {
        if (inward < 0) return null;
      } else if (rate > 0) {
        lo = Math.max(lo, -inward / rate);
      } else {
        hi = Math.min(hi, -inward / rate);
      }
    }
    return lo < hi ? [lo, hi] : null;
  }

  function hatch(group, outline) {
    var xs = outline.map(function (p) { return p[0]; });
    var ys = outline.map(function (p) { return p[1]; });
    var x0 = Math.min.apply(null, xs);
    var x1 = Math.max.apply(null, xs);
    var y0 = Math.min.apply(null, ys);
    var y1 = Math.max.apply(null, ys);
    var step = HATCH_GAP * Math.SQRT2;
    for (var c = x0 + y0 + step; c < x1 + y1; c += step) {
      var span = clipLine(outline, [c - y0, y0], -1, 1);
      if (!span) continue;
      var line = svgNode("line");
      line.setAttribute("class", "flow-hatch");
      line.setAttribute("x1", round1(c - y0 - span[0]));
      line.setAttribute("y1", round1(y0 + span[0]));
      line.setAttribute("x2", round1(c - y0 - span[1]));
      line.setAttribute("y2", round1(y0 + span[1]));
      group.appendChild(line);
    }
  }

  function arrowhead(group, rng, points) {
    var tip = points[points.length - 1];
    var from = points[points.length - 2];
    var back = Math.atan2(from[1] - tip[1], from[0] - tip[0]);
    [-1, 1].forEach(function (side) {
      var angle = back + side * ARROW_SPREAD;
      stroke(group, rng, [[tip[0] + ARROW_LENGTH * Math.cos(angle), tip[1] + ARROW_LENGTH * Math.sin(angle)], tip]);
    });
  }

  function flowMeasurer(svg) {
    var probe = svgNode("text");
    probe.setAttribute("class", "flow-label");
    svg.appendChild(probe);
    var widths = new Map();
    function measure(text) {
      if (!widths.has(text)) {
        probe.textContent = text;
        widths.set(text, probe.getComputedTextLength());
      }
      return widths.get(text);
    }
    return { measure: measure, done: function () { svg.removeChild(probe); } };
  }

  function buildFlow(flow, state) {
    if (!window.PadFlow) throw new Error("the flow parser did not load.");
    state.model = window.PadFlow.parse(flow);
    state.svg = state.drawing.querySelector("svg.pad-ink");
    if (!state.svg) throw new Error("the drawing has no ink layer.");
    state.svg.setAttribute("class", "pad-ink pad-flow");
    state.drawing.classList.add("pad-drawing-flow");
  }

  function traceFlow(state) {
    var svg = state.svg;
    svg.textContent = "";
    state.unmeasured = state.drawing.clientWidth === 0;
    var measurer = flowMeasurer(svg);
    var plan = window.PadFlow.layout(state.model, measurer.measure);
    measurer.done();
    state.plan = plan;
    svg.setAttribute("viewBox", "0 0 " + round1(plan.width) + " " + round1(plan.height));
    var rng = mulberry32(state.seed);
    plan.steps.forEach(function (step) {
      var group = svgNode("g");
      group.setAttribute("data-kind", step.shape);
      group.setAttribute("data-step-id", step.id);
      var outline = stepOutline(step);
      stroke(group, rng, outline);
      if (step.accent) {
        group.setAttribute("class", "flow-accent");
        hatch(group, outline);
      }
      svg.appendChild(group);
      step.lines.forEach(function (line) {
        var text = labelText(group, line);
        if (step.accent) {
          var box = text.getBBox();
          group.insertBefore(haloRect(box.x - HALO_PAD, box.y - HALO_PAD, box.width + 2 * HALO_PAD, box.height + 2 * HALO_PAD), text);
        }
      });
    });
    plan.arrows.forEach(function (arrow) {
      var group = svgNode("g");
      group.setAttribute("data-kind", arrow.loop ? "loop" : "arrow");
      group.setAttribute("data-arrow-index", String(arrow.index));
      stroke(group, rng, arrow.points, arrow.dashed ? "flow-dashed" : null);
      arrowhead(group, rng, arrow.points);
      if (arrow.label) drawLabel(group, arrow.label);
      svg.appendChild(group);
    });
    fitFlow(state);
  }

  function labelText(group, line) {
    var text = svgNode("text");
    text.setAttribute("class", "flow-label");
    text.setAttribute("x", round1(line.x));
    text.setAttribute("y", round1(line.y));
    text.textContent = line.text;
    group.appendChild(text);
    return text;
  }

  // The card-coloured patch set under a label so no line or hatch shows through it.
  function haloRect(x, y, w, h) {
    var halo = svgNode("rect");
    halo.setAttribute("class", "flow-halo");
    halo.setAttribute("x", round1(x));
    halo.setAttribute("y", round1(y));
    halo.setAttribute("width", round1(w));
    halo.setAttribute("height", round1(h));
    return halo;
  }

  function drawLabel(group, label) {
    group.appendChild(haloRect(label.halo.x, label.halo.y, label.halo.w, label.halo.h));
    label.lines.forEach(function (line) { labelText(group, line); });
  }

  function fitFlow(state) {
    var plan = state.plan;
    if (!plan) return;
    var avail = state.drawing.clientWidth;
    var scale = avail > 0 ? Math.max(MIN_SCALE, Math.min(1, avail / plan.width)) : 1;
    state.svg.style.width = round1(plan.width * scale) + "px";
    state.svg.style.height = round1(plan.height * scale) + "px";
  }

  function prepare(drawing, option, seed) {
    var isFlow = !!option && typeof option === "object" && Object.prototype.hasOwnProperty.call(option, "flow");
    var layout = option ? (isFlow ? option.flow : option.layout) : undefined;
    var state = { drawing: drawing, layout: layout, flow: isFlow, seed: seed, dead: false, items: [], svg: null, observer: null };
    guarded(state, function () {
      if (isFlow) {
        buildFlow(layout, state);
        return;
      }
      var ctx = { items: state.items };
      var root = document.createElement("div");
      root.className = "pen-layout";
      root.appendChild(buildBlock(layout, [], ctx));
      state.svg = drawing.querySelector("svg.pad-ink");
      if (!state.svg) throw new Error("the drawing has no ink layer.");
      drawing.insertBefore(root, state.svg);
    });
    return state;
  }

  function observe(state) {
    if (state.dead || typeof ResizeObserver !== "function") return;
    state.observer = new ResizeObserver(function () {
      guarded(state, function () { if (state.flow) fitFlow(state); else trace(state); });
    });
    state.observer.observe(state.drawing);
  }

  var drawnStates = new WeakMap();

  // A flow measures its labels when it is traced, and a question that was hidden then measured every
  // label as nothing. Showing the question again traces such a flow once more, from the same seed.
  function shown(panel) {
    (drawnStates.get(panel) || []).forEach(function (state) {
      if (state.flow && state.unmeasured && !state.dead && state.drawing.clientWidth > 0) {
        guarded(state, function () { traceFlow(state); });
      }
    });
  }

  function drawQuestion(panel, question, qIndex) {
    var states = [];
    drawnStates.set(panel, states);
    var drawings = panel.querySelectorAll(".pad-drawing");
    for (var i = 0; i < drawings.length; i++) {
      var drawing = drawings[i];
      var oIndex = Number(drawing.getAttribute("data-option"));
      var option = question && question.options ? question.options[oIndex] : undefined;
      states.push(prepare(drawing, option, seedFor(qIndex, oIndex)));
    }
    var ready = document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve();
    return ready.catch(function () {}).then(function () {
      states.forEach(function (state) {
        guarded(state, function () { trace(state); });
        observe(state);
      });
    });
  }

  window.PadPen = { drawQuestion: drawQuestion, shown: shown };
})();

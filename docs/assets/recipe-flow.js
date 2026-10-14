// Interactive flow diagrams for recipes on the documentation site.
//
// Each <div class="recipe-flow" data-recipe="slug"> is replaced by a diagram
// drawn from data/recipes/<slug>.json, which `aa-recipe site-data` writes.
// Layout is done by dagre; nodes are HTML so they can expand in place, and
// edges are SVG underneath them. The drawing sits on a stage that is zoomed
// and panned with a CSS transform. Each step's recipe inputs, and its outputs
// used later, are drawn as one box each. A switch, shared by every diagram on
// the page and remembered between visits, shows the steps of other recipes a
// sub-recipe connects to. Without JavaScript, or if the data cannot be loaded,
// the plain step list inside the div stays visible.
// It reads graph schema 2 and ignores the keys only the client uses.

(function () {
  "use strict";

  var script = document.currentScript;
  var siteRoot = script ? new URL("..", script.src).href : "/";
  var SVG = "http://www.w3.org/2000/svg";
  var MAX_VALUE = 80;
  var MIN_ZOOM = 0.15;
  var MAX_ZOOM = 2.5;
  var PAD = 24;
  // Versioned so a setting saved under an earlier default does not override
  // the current one.
  var PREFS_KEY = "aa-recipe-flow-2";
  var diagrams = [];

  // Material Design icon paths (Apache 2.0).
  var ICONS = {
    zoomIn: "M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z",
    zoomOut: "M19 13H5v-2h14v2z",
    fit: "M3 5v4h2V5h4V3H5c-1.1 0-2 .9-2 2zm2 10H3v4c0 1.1.9 2 2 2h4v-2H5v-4zm14 4h-4v2h4c1.1 0 2-.9 2-2v-4h-2v4zm0-16h-4v2h4v4h2V5c0-1.1-.9-2-2-2z",
    expand: "M12 5.83 15.17 9l1.41-1.41L12 3 7.41 7.59 8.83 9 12 5.83zm0 12.34L8.83 15l-1.41 1.41L12 21l4.59-4.59L15.17 15 12 18.17z",
    collapse: "M7.41 18.59 8.83 20 12 16.83 15.17 20l1.41-1.41L12 14l-4.59 4.59zm9.18-13.18L15.17 4 12 7.17 8.83 4 7.41 5.41 12 10l4.59-4.59z",
    fullscreen: "M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z",
    exitFullscreen: "M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"
  };

  // --- Shared view options -------------------------------------------------

  var prefs = { context: true };
  try {
    var stored = JSON.parse(window.localStorage.getItem(PREFS_KEY) || "{}");
    if (typeof stored.context === "boolean") prefs.context = stored.context;
  } catch (e) {
    // Storage can be unavailable (private windows); the defaults stand.
  }

  function setPref(name, value) {
    prefs[name] = value;
    try {
      window.localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    } catch (e) {
      // Not remembered, but still applied to this page.
    }
    diagrams.forEach(function (d) { d.refresh(); });
  }

  // --- Small helpers -------------------------------------------------------

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function svg(tag, attrs) {
    var node = document.createElementNS(SVG, tag);
    Object.keys(attrs || {}).forEach(function (key) {
      node.setAttribute(key, attrs[key]);
    });
    return node;
  }

  function icon(name) {
    var s = svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true", "class": "rf-icon" });
    s.appendChild(svg("path", { d: ICONS[name] }));
    return s;
  }

  function show(value) {
    if (value === null || value === undefined) return "";
    return typeof value === "string" ? value : JSON.stringify(value);
  }

  function clipped(className, value) {
    var text = show(value);
    var long = text.length > MAX_VALUE;
    var span = el("span", className, long ? text.slice(0, MAX_VALUE - 1) + "…" : text);
    if (long) span.title = text;
    return span;
  }

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  function link(className, text, page, title) {
    var a = el("a", className, text);
    a.href = siteRoot + page;
    if (title) a.title = title;
    return a;
  }

  // d3's curveBasis: a smooth B-spline through dagre's edge points.
  function basisPath(points) {
    if (points.length < 3) {
      return "M" + points.map(function (p) { return p.x + "," + p.y; }).join("L");
    }
    var x0 = points[0].x, y0 = points[0].y, x1 = points[1].x, y1 = points[1].y;
    var d = "M" + x0 + "," + y0 + "L" + (5 * x0 + x1) / 6 + "," + (5 * y0 + y1) / 6;
    function curve(x, y) {
      d += "C" + (2 * x0 + x1) / 3 + "," + (2 * y0 + y1) / 3 + " " +
        (x0 + 2 * x1) / 3 + "," + (y0 + 2 * y1) / 3 + " " +
        (x0 + 4 * x1 + x) / 6 + "," + (y0 + 4 * y1 + y) / 6;
      x0 = x1; y0 = y1; x1 = x; y1 = y;
    }
    for (var i = 2; i < points.length; i++) curve(points[i].x, points[i].y);
    curve(x1, y1);
    return d + "L" + x1 + "," + y1;
  }

  // --- What to draw --------------------------------------------------------

  // The nodes and edges to draw. The graph from the data file is never
  // changed; the view builds on a copy.
  function viewGraph(graph) {
    var nodes = graph.nodes.map(function (n) { return Object.assign({}, n); });
    var edges = graph.edges.map(function (e) { return Object.assign({}, e); });
    var context = graph.context;
    if (prefs.context && context) addContext(nodes, edges, context);
    return combine(nodes, edges, graph.groups);
  }

  function addContext(nodes, edges, context) {
    var byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });
    Object.keys(context.values).forEach(function (id) {
      if (byId[id]) byId[id].value = context.values[id];
    });
    var added = {};
    function contextNode(item, before) {
      var id = "ctx:" + item.step;
      if (added[id]) return id;
      added[id] = true;
      var node = { id: id, kind: "context", label: item.step, op: item.op, title: item.title, page: item.page };
      if (before) nodes.unshift(node); else nodes.push(node);
      return id;
    }
    context.upstream.forEach(function (u) {
      edges.push({ source: contextNode(u, true), target: u.target, kind: "context", label: u.output || "", type: null });
    });
    context.downstream.forEach(function (d) {
      edges.push({ source: d.source, target: contextNode(d, false), kind: "context", label: d.input, type: null });
    });
  }

  // One box of inputs per step, and one box of leftover outputs per step.
  function combine(nodes, edges, groups) {
    var byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });
    var boxes = {};
    var boxesOf = {};
    var kept = [];

    function box(id, kind, group) {
      if (!boxes[id]) boxes[id] = { id: id, kind: kind, rows: [], group: group };
      return boxes[id];
    }

    edges.forEach(function (edge) {
      var source = byId[edge.source], target = byId[edge.target];
      if (source && source.kind === "input" && target && edge.kind !== "map_over" && edge.kind !== "context") {
        var b = box("cin:" + target.id, "inputs", target.group);
        var name = source.label.replace(/^inputs\./, "");
        b.rows.push({ name: name, into: edge.target_input, type: source.type, value: source.value });
        (boxesOf[source.id] = boxesOf[source.id] || []).push(b.id);
        if (!b.edge) {
          b.edge = { source: b.id, target: target.id, kind: "param", label: "", type: null };
          kept.push(b.edge);
        }
        source.absorbed = (source.absorbed || 0) + 1;
        return;
      }
      if (target && target.kind === "output" && edge.kind === "exit") {
        var o = box("cout:" + source.id, "outputs", source.group);
        o.rows.push({ name: edge.source_output, type: target.type, declared: target.declared });
        (boxesOf[target.id] = boxesOf[target.id] || []).push(o.id);
        if (!o.edge) {
          o.edge = { source: source.id, target: o.id, kind: "exit", label: "", type: null };
          kept.push(o.edge);
        }
        target.absorbed = 1;
        return;
      }
      kept.push(edge);
    });

    // An input still feeding something unboxed (the list a group maps over)
    // stays as its own node. Context edges do not count: they move to the
    // boxes below.
    var used = {};
    kept.forEach(function (e) {
      if (e.kind === "context") return;
      used[e.source] = true;
      used[e.target] = true;
    });
    var out = nodes.filter(function (n) {
      return !(n.absorbed && !used[n.id]);
    });

    // Context edges into or out of a boxed node move to its boxes.
    var moved = [];
    kept.forEach(function (e) {
      if (e.kind !== "context") { moved.push(e); return; }
      var targets = byId[e.target] && !used[e.target] ? boxesOf[e.target] : null;
      var sources = byId[e.source] && !used[e.source] ? boxesOf[e.source] : null;
      if (targets) {
        targets.forEach(function (id) { moved.push(Object.assign({}, e, { target: id })); });
      } else if (sources) {
        sources.forEach(function (id) { moved.push(Object.assign({}, e, { source: id })); });
      } else {
        moved.push(e);
      }
    });

    Object.keys(boxes).forEach(function (id) {
      delete boxes[id].edge;
      out.push(boxes[id]);
    });
    return { nodes: out, edges: moved, groups: groups };
  }

  // --- Node contents -------------------------------------------------------

  function table(title, headers, rows) {
    var section = el("div", "rf-section");
    if (title) section.appendChild(el("div", "rf-section-title", title));
    var t = el("table", "rf-table");
    var head = el("tr");
    headers.forEach(function (h) { head.appendChild(el("th", null, h)); });
    t.appendChild(head);
    rows.forEach(function (cells) {
      var tr = el("tr");
      cells.forEach(function (cell, i) {
        var td = el("td", "rf-col-" + i);
        if (cell instanceof Node) td.appendChild(cell); else td.textContent = cell;
        tr.appendChild(td);
      });
      t.appendChild(tr);
    });
    section.appendChild(t);
    return section;
  }

  function typeCell(type, units) {
    var text = type || "";
    if (units) text += (text ? " " : "") + "(" + units + ")";
    return el("span", "rf-type", text);
  }

  function firstSentence(text) {
    var end = text.indexOf(". ");
    return end === -1 ? text : text.slice(0, end + 1);
  }

  function resizeFrom(node) {
    node.dispatchEvent(new CustomEvent("rf-resize", { bubbles: true, detail: { anchor: node.closest(".rf-node") } }));
  }

  function stepBody(node) {
    var body = el("div", "rf-body");
    if (node.description) body.appendChild(el("p", "rf-description", firstSentence(node.description)));
    if (node.custom && node.callable_path) {
      var call = el("p", "rf-description");
      call.appendChild(document.createTextNode("Calls "));
      call.appendChild(el("code", null, node.callable_path));
      body.appendChild(call);
    }

    var set = node.params.filter(function (p) { return p.set; });
    var unset = node.params.filter(function (p) { return !p.set; });
    if (set.length) {
      body.appendChild(table("Parameters", ["name", "value", "type"], set.map(function (p) {
        return [el("code", null, p.name), clipped("rf-value", p.value), typeCell(p.type, p.units)];
      })));
    }
    if (unset.length) {
      var more = el("button", "rf-more-toggle", "Show " + unset.length + " more parameter" + (unset.length > 1 ? "s" : "") + " left at defaults");
      more.type = "button";
      var defaults = table("", ["name", "default", "type"], unset.map(function (p) {
        return [el("code", null, p.name), clipped("rf-value", p.default), typeCell(p.type, p.units)];
      }));
      defaults.hidden = true;
      more.addEventListener("click", function () {
        defaults.hidden = !defaults.hidden;
        more.textContent = (defaults.hidden ? "Show " : "Hide ") + unset.length + " more parameter" + (unset.length > 1 ? "s" : "") + " left at defaults";
        resizeFrom(more);
      });
      body.appendChild(more);
      body.appendChild(defaults);
    }
    if (node.sweep) {
      body.appendChild(table("Sweep (" + node.sweep.mode + ")", ["name", "values"], Object.keys(node.sweep.params).map(function (k) {
        return [el("code", null, k), clipped("rf-value", node.sweep.params[k])];
      })));
    }
    var wired = node.inputs.filter(function (p) { return p.value !== null; });
    if (wired.length) {
      body.appendChild(table("Inputs", ["name", "from", "type"], wired.map(function (p) {
        return [el("code", null, p.name), clipped("rf-value", p.value), typeCell(p.type)];
      })));
    }
    if (node.outputs.length) {
      body.appendChild(table("Outputs", ["name", "type"], node.outputs.map(function (p) {
        return [el("code", null, p.name + (p.disposed ? " (scratch)" : "")), typeCell(p.type)];
      })));
    }
    if (node.op_url) body.appendChild(link("rf-more", "Op reference →", node.op_url));
    return body;
  }

  function stepNode(node, open) {
    var box = el("div", "rf-node rf-step");
    if (node.custom) box.classList.add("rf-custom");
    if (node.sink) box.classList.add("rf-sink");

    var head = el("div", "rf-head");
    var toggle = el("button", "rf-toggle");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.title = "Show parameters, inputs and outputs";
    toggle.appendChild(el("span", "rf-caret", "▸"));
    toggle.appendChild(el("span", "rf-id", node.id));
    head.appendChild(toggle);

    if (node.op_url) {
      head.appendChild(link("rf-op", node.custom ? "custom, extends " + node.extends : node.op, node.op_url,
        "Open the op reference for " + (node.extends || node.op)));
    } else {
      head.appendChild(el("span", "rf-op rf-op-custom", "custom step"));
    }

    var badges = [];
    if (node.mapped) badges.push(["per item", "rf-badge-map"]);
    if (node.collector) badges.push(["collects", "rf-badge-collect"]);
    if (node.sweep) badges.push(["sweep", ""]);
    if (node.checkpoint === "always" || node.checkpoint === "save") badges.push(["checkpoint", "rf-badge-ckpt"]);
    if (node.sink) badges.push(["writes files", "rf-badge-sink"]);
    if (badges.length) {
      var row = el("div", "rf-badges");
      badges.forEach(function (b) { row.appendChild(el("span", "rf-badge " + b[1], b[0])); });
      head.appendChild(row);
    }
    box.appendChild(head);

    var body = stepBody(node);
    box.appendChild(body);
    setOpen(box, open);
    toggle.addEventListener("click", function () {
      setOpen(box, body.hidden);
      resizeFrom(box);
    });
    return box;
  }

  function setOpen(box, open) {
    var body = box.querySelector(".rf-body");
    if (!body) return;
    body.hidden = !open;
    box.classList.toggle("rf-open", open);
    box.querySelector(".rf-toggle").setAttribute("aria-expanded", String(open));
  }

  function pillNode(node) {
    var box = el("div", "rf-node rf-pill rf-" + node.kind);
    box.appendChild(el("span", "rf-pill-label", node.label));
    if (node.type) box.appendChild(el("span", "rf-pill-type", node.type));
    if (node.value) box.appendChild(el("span", "rf-pill-value", node.value));
    var tip = [];
    if (node.kind === "input") tip.push("Recipe input");
    if (node.kind === "external") tip.push("Output of a step in another file");
    if (node.kind === "output") tip.push(node.declared ? "Recipe output" : "Not used inside this recipe");
    if (node.description) tip.push(node.description);
    if (node.default !== undefined && node.default !== null) tip.push("Default: " + show(node.default));
    box.title = tip.join("\n");
    return box;
  }

  function boxNode(node) {
    var box = el("div", "rf-node rf-box rf-box-" + node.kind);
    box.appendChild(el("div", "rf-box-title", node.kind === "inputs" ? "Recipe inputs" : "Outputs used later"));
    var list = el("ul", "rf-box-rows");
    node.rows.forEach(function (row) {
      var li = el("li");
      li.appendChild(el("code", null, row.name));
      if (row.into && row.into !== row.name) li.appendChild(el("span", "rf-box-into", "→ " + row.into));
      if (row.type) li.appendChild(el("span", "rf-type", row.type));
      if (row.value) li.appendChild(el("span", "rf-box-value", row.value));
      list.appendChild(li);
    });
    box.appendChild(list);
    return box;
  }

  function contextNode(node) {
    var box = el("div", "rf-node rf-context");
    box.appendChild(el("div", "rf-context-id", node.label));
    box.appendChild(el("div", "rf-context-op", node.op));
    if (node.page !== null && node.page !== undefined) {
      box.appendChild(link("rf-context-link", node.title + " →", node.page, "Open the page for " + node.title));
    } else {
      box.appendChild(el("div", "rf-context-link", node.title));
    }
    return box;
  }

  function levelNode(node) {
    var box = el("div", "rf-node rf-level" + (node.inline ? " rf-level-inline" : ""));
    box.appendChild(el("div", "rf-level-title", node.label));
    var meta = node.inline ? node.op : (node.steps + " step" + (node.steps === 1 ? "" : "s"));
    box.appendChild(el("div", "rf-level-meta", meta));
    if (node.page !== null && node.page !== undefined) {
      box.appendChild(link("rf-level-link", "Open →", node.page, "Open the page for " + node.label));
    }
    return box;
  }

  function makeNode(node, open) {
    switch (node.kind) {
      case "step": return stepNode(node, open);
      case "inputs": case "outputs": return boxNode(node);
      case "context": return contextNode(node);
      case "level": return levelNode(node);
      default: return pillNode(node);
    }
  }

  // --- Diagram -------------------------------------------------------------

  function Diagram(container, graph) {
    this.container = container;
    this.graph = graph;
    this.view = { k: 1, x: 0, y: 0 };
    this.size = { width: 0, height: 0 };
    this.positions = {};
    this.open = {};
    this.buildFrame();
    this.render();
    this.initialView();
  }

  Diagram.prototype.buildFrame = function () {
    var self = this;
    var graph = this.graph;
    var wrap = el("div", "rf-wrap");
    this.wrap = wrap;

    var bar = el("div", "rf-toolbar");
    var steps = graph.nodes.filter(function (n) { return n.kind === "step" || n.kind === "level"; }).length;
    var noun = graph.overview ? "part" : "step";
    bar.appendChild(el("span", "rf-summary", steps + " " + noun + (steps === 1 ? "" : "s")));

    var options = el("div", "rf-options");
    if (graph.context) {
      this.contextChip = this.chip("Show other recipe steps",
        "Show the steps of other recipes that feed this one or read what it produces", "context");
      options.appendChild(this.contextChip);
    }
    bar.appendChild(options);

    var tools = el("div", "rf-tools");
    this.zoomLabel = el("button", "rf-zoom-label", "100%");
    this.zoomLabel.type = "button";
    this.zoomLabel.title = "Reset to 100%";
    this.zoomLabel.addEventListener("click", function () { self.zoomTo(1); });
    tools.appendChild(this.tool("zoomOut", "Zoom out (−)", function () { self.zoomBy(1 / 1.25); }));
    tools.appendChild(this.zoomLabel);
    tools.appendChild(this.tool("zoomIn", "Zoom in (+)", function () { self.zoomBy(1.25); }));
    tools.appendChild(this.tool("fit", "Fit the whole diagram (0)", function () { self.fit(); }));
    if (!graph.overview) {
      tools.appendChild(el("span", "rf-sep"));
      tools.appendChild(this.tool("expand", "Expand every step", function () { self.setAll(true); }));
      tools.appendChild(this.tool("collapse", "Collapse every step", function () { self.setAll(false); }));
    }
    tools.appendChild(el("span", "rf-sep"));
    this.fullButton = this.tool("fullscreen", "Full screen (F)", function () { self.toggleFullscreen(); });
    tools.appendChild(this.fullButton);
    bar.appendChild(tools);
    wrap.appendChild(bar);

    this.legendRow = el("div", "rf-legend");
    wrap.appendChild(this.legendRow);

    var canvas = el("div", "rf-canvas");
    canvas.tabIndex = 0;
    canvas.setAttribute("role", "application");
    canvas.setAttribute("aria-label", "Flow diagram of " + graph.title + ". Arrow keys pan, plus and minus zoom, 0 fits.");
    this.canvas = canvas;

    this.stage = el("div", "rf-stage");
    this.edgeLayer = svg("svg", { "class": "rf-edges" });
    var defs = svg("defs");
    this.markerId = "rf-arrow-" + graph.slug;
    var marker = svg("marker", {
      id: this.markerId, viewBox: "0 0 10 10", refX: "9", refY: "5",
      markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse"
    });
    marker.appendChild(svg("path", { d: "M0,0 L10,5 L0,10 z", "class": "rf-arrowhead" }));
    defs.appendChild(marker);
    this.edgeLayer.appendChild(defs);
    this.groupLayer = el("div", "rf-groups");
    this.labelLayer = el("div", "rf-labels");
    this.nodeLayer = el("div", "rf-nodes");
    this.stage.appendChild(this.groupLayer);
    this.stage.appendChild(this.edgeLayer);
    this.stage.appendChild(this.labelLayer);
    this.stage.appendChild(this.nodeLayer);
    canvas.appendChild(this.stage);
    canvas.appendChild(el("div", "rf-hint", "Drag to move · Ctrl + scroll to zoom"));
    this.toast = el("div", "rf-toast", "Hold Ctrl (⌘ on a Mac) and scroll to zoom");
    canvas.appendChild(this.toast);
    wrap.appendChild(canvas);

    wrap.addEventListener("rf-resize", function (event) {
      self.layout(event.detail && event.detail.anchor);
    });
    this.bindNavigation();
    this.container.appendChild(wrap);
  };

  Diagram.prototype.chip = function (text, title, pref) {
    var button = el("button", "rf-chip", text);
    button.type = "button";
    button.title = title;
    button.addEventListener("click", function () { setPref(pref, !prefs[pref]); });
    return button;
  };

  Diagram.prototype.tool = function (name, title, action) {
    var button = el("button", "rf-tool");
    button.type = "button";
    button.title = title;
    button.setAttribute("aria-label", title);
    button.appendChild(icon(name));
    button.addEventListener("click", action);
    return button;
  };

  Diagram.prototype.updateChips = function () {
    if (this.contextChip) this.contextChip.setAttribute("aria-pressed", String(prefs.context));
  };

  Diagram.prototype.updateLegend = function (view) {
    var kinds = {};
    view.nodes.forEach(function (n) { kinds[n.kind] = true; });
    var items = [];
    if (kinds.step) items.push(["rf-key-step", "Step: click to expand"]);
    if (kinds.level) items.push(["rf-key-step", "Part of the recipe: open its page"]);
    if (kinds.input || kinds.inputs) items.push(["rf-key-input", "Recipe input"]);
    if (kinds.external) items.push(["rf-key-external", "From another recipe"]);
    if (kinds.context) items.push(["rf-key-context", "Step in another recipe"]);
    if (kinds.output || kinds.outputs) items.push(["rf-key-output", "Result used after this recipe"]);
    if (view.groups.length) items.push(["rf-key-group", "Runs once per item"]);
    this.legendRow.textContent = "";
    var row = this.legendRow;
    items.forEach(function (item) {
      var key = el("span", "rf-key");
      key.appendChild(el("span", "rf-swatch " + item[0]));
      key.appendChild(document.createTextNode(item[1]));
      row.appendChild(key);
    });
  };

  // Rebuild the drawing for the current view options.
  Diagram.prototype.render = function () {
    var self = this;
    var view = viewGraph(this.graph);
    this.current = view;
    this.updateChips();
    this.updateLegend(view);

    this.nodeLayer.textContent = "";
    this.labelLayer.textContent = "";
    this.groupLayer.textContent = "";
    Array.prototype.slice.call(this.edgeLayer.querySelectorAll("path.rf-edge")).forEach(function (p) { p.remove(); });

    this.nodes = {};
    this.edgesOf = {};
    view.nodes.forEach(function (node) {
      var box = makeNode(node, !!self.open[node.id]);
      box.dataset.id = node.id;
      self.nodes[node.id] = box;
      self.edgesOf[node.id] = [];
      self.nodeLayer.appendChild(box);
      box.addEventListener("mouseenter", function () { self.highlight(node.id); });
      box.addEventListener("mouseleave", function () { self.highlight(null); });
    });

    this.groups = {};
    view.groups.forEach(function (group) {
      var box = el("div", "rf-group");
      var label = el("div", "rf-group-label", group.label);
      self.groups[group.id] = { box: box, label: label };
      self.groupLayer.appendChild(box);
      self.labelLayer.appendChild(label);
    });

    this.paths = [];
    this.labels = view.edges.map(function (edge, i) {
      var label = el("div", "rf-edge-label rf-edge-" + edge.kind);
      if (edge.label) label.appendChild(el("span", "rf-edge-name", edge.label));
      if (edge.type && edge.kind !== "depends") label.appendChild(el("span", "rf-edge-type", edge.type));
      if (!label.childNodes.length) label.classList.add("rf-empty");
      self.labelLayer.appendChild(label);
      var path = svg("path", { "class": "rf-edge rf-edge-" + edge.kind, "marker-end": "url(#" + self.markerId + ")" });
      self.edgeLayer.appendChild(path);
      self.paths.push(path);
      if (self.edgesOf[edge.source]) self.edgesOf[edge.source].push(i);
      if (self.edgesOf[edge.target]) self.edgesOf[edge.target].push(i);
      return label;
    });

    this.layout();
  };

  // After a view option changes: redraw, then show the top of the new
  // drawing at the current zoom.
  Diagram.prototype.refresh = function () {
    this.render();
    var w = this.canvas.clientWidth || 600;
    this.view.x = (w - this.size.width * this.view.k) / 2;
    this.view.y = 0;
    this.apply();
  };

  Diagram.prototype.setAll = function (open) {
    var self = this;
    this.current.nodes.forEach(function (node) {
      if (node.kind !== "step") return;
      self.open[node.id] = open;
      setOpen(self.nodes[node.id], open);
    });
    this.layout();
  };

  Diagram.prototype.highlight = function (id) {
    var self = this;
    if (id !== null && this.canvas.classList.contains("rf-dragging")) return;
    this.stage.classList.toggle("rf-focus", id !== null);
    Array.prototype.forEach.call(this.stage.querySelectorAll(".rf-hot"), function (n) {
      n.classList.remove("rf-hot");
    });
    if (id === null) return;
    this.nodes[id].classList.add("rf-hot");
    this.edgesOf[id].forEach(function (i) {
      var edge = self.current.edges[i];
      self.paths[i].classList.add("rf-hot");
      self.labels[i].classList.add("rf-hot");
      [edge.source, edge.target].forEach(function (end) {
        if (self.nodes[end]) self.nodes[end].classList.add("rf-hot");
      });
    });
  };

  // Lay the drawing out. With an anchor node, the view shifts so that node
  // stays where it was on screen, so expanding a step does not jump.
  Diagram.prototype.layout = function (anchor) {
    var self = this;
    var view = this.current;
    var anchorId = anchor && anchor.dataset ? anchor.dataset.id : null;
    if (anchorId && this.nodes[anchorId].classList.contains("rf-step")) {
      this.open[anchorId] = this.nodes[anchorId].classList.contains("rf-open");
    }
    var before = anchorId ? this.positions[anchorId] : null;

    var g = new dagre.graphlib.Graph({ compound: true, multigraph: true });
    g.setGraph({ rankdir: "TB", nodesep: 28, ranksep: 46, edgesep: 14, marginx: PAD, marginy: PAD });
    g.setDefaultEdgeLabel(function () { return {}; });

    view.nodes.forEach(function (node) {
      var box = self.nodes[node.id];
      g.setNode(node.id, { width: box.offsetWidth, height: box.offsetHeight });
    });
    view.groups.forEach(function (group) {
      var label = self.groups[group.id].label;
      g.setNode(group.id, { paddingTop: label.offsetHeight + 18, paddingLeft: 14, paddingRight: 14, paddingBottom: 14 });
    });
    view.nodes.forEach(function (node) {
      if (node.group && self.groups[node.group]) g.setParent(node.id, node.group);
    });
    view.groups.forEach(function (group) {
      group.members.forEach(function (member) {
        if (self.nodes[member]) g.setParent(member, group.id);
      });
    });
    view.edges.forEach(function (edge, i) {
      var label = self.labels[i];
      g.setEdge(edge.source, edge.target, {
        width: label.offsetWidth, height: label.offsetHeight, labelpos: "c"
      }, "e" + i);
    });

    dagre.layout(g);

    var size = g.graph();
    this.size = { width: size.width, height: size.height };
    this.stage.style.width = size.width + "px";
    this.stage.style.height = size.height + "px";
    this.edgeLayer.setAttribute("width", size.width);
    this.edgeLayer.setAttribute("height", size.height);

    view.nodes.forEach(function (node) {
      var pos = g.node(node.id);
      var box = self.nodes[node.id];
      var left = Math.round(pos.x - pos.width / 2);
      var top = Math.round(pos.y - pos.height / 2);
      box.style.left = left + "px";
      box.style.top = top + "px";
      self.positions[node.id] = { x: left, y: top };
    });
    view.groups.forEach(function (group) {
      var pos = g.node(group.id);
      var parts = self.groups[group.id];
      var left = Math.round(pos.x - pos.width / 2);
      var top = Math.round(pos.y - pos.height / 2);
      parts.box.style.left = left + "px";
      parts.box.style.top = top + "px";
      parts.box.style.width = Math.round(pos.width) + "px";
      parts.box.style.height = Math.round(pos.height) + "px";
      parts.label.style.left = (left + 10) + "px";
      parts.label.style.top = (top + 8) + "px";
    });
    view.edges.forEach(function (edge, i) {
      var route = g.edge({ v: edge.source, w: edge.target, name: "e" + i });
      self.paths[i].setAttribute("d", basisPath(route.points));
      var label = self.labels[i];
      label.style.left = Math.round(route.x - label.offsetWidth / 2) + "px";
      label.style.top = Math.round(route.y - label.offsetHeight / 2) + "px";
    });

    if (before) {
      var after = this.positions[anchorId];
      this.view.x += (before.x - after.x) * this.view.k;
      this.view.y += (before.y - after.y) * this.view.k;
      this.apply();
    }
  };

  // --- Zoom and pan --------------------------------------------------------

  // Whole-pixel offsets keep text sharp; the stage is not given its own
  // compositing layer, so the browser redraws it crisply at each zoom.
  Diagram.prototype.apply = function () {
    var v = this.view;
    var x = Math.round(v.x), y = Math.round(v.y);
    this.stage.style.transform = "translate(" + x + "px," + y + "px) scale(" + v.k + ")";
    var grid = 16 * v.k;
    this.canvas.style.backgroundSize = grid + "px " + grid + "px";
    this.canvas.style.backgroundPosition = x + "px " + y + "px";
    this.zoomLabel.textContent = Math.round(v.k * 100) + "%";
  };

  Diagram.prototype.zoomAt = function (k, cx, cy) {
    var v = this.view;
    k = clamp(k, MIN_ZOOM, MAX_ZOOM);
    v.x = cx - (cx - v.x) * (k / v.k);
    v.y = cy - (cy - v.y) * (k / v.k);
    v.k = k;
    this.apply();
  };

  Diagram.prototype.zoomBy = function (factor) {
    this.zoomAt(this.view.k * factor, this.canvas.clientWidth / 2, this.canvas.clientHeight / 2);
  };

  Diagram.prototype.zoomTo = function (k) {
    this.zoomAt(k, this.canvas.clientWidth / 2, this.canvas.clientHeight / 2);
  };

  Diagram.prototype.fit = function () {
    var w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    var k = clamp(Math.min(w / this.size.width, h / this.size.height, 1), MIN_ZOOM, MAX_ZOOM);
    this.view = { k: k, x: (w - this.size.width * k) / 2, y: Math.max(0, (h - this.size.height * k) / 2) };
    this.apply();
  };

  // First view: the whole diagram when it fits at a readable size, otherwise
  // as wide as the page allows, starting from the top. The canvas is sized
  // to the drawing, up to most of the window.
  Diagram.prototype.initialView = function () {
    var w = this.canvas.clientWidth || 600;
    var maxHeight = window.innerHeight * 0.72;
    var whole = Math.min(1, w / this.size.width, maxHeight / this.size.height);
    var k = whole >= 0.7 ? whole : clamp(Math.min(1, w / this.size.width), 0.55, 1);
    var height = clamp(this.size.height * k, 260, maxHeight);
    this.canvas.style.height = Math.round(height) + "px";
    // Centered horizontally; a drawing wider than the canvas shows its middle.
    this.view = { k: k, x: (w - this.size.width * k) / 2, y: Math.max(0, (height - this.size.height * k) / 2) };
    this.apply();
  };

  Diagram.prototype.pan = function (dx, dy) {
    this.view.x += dx;
    this.view.y += dy;
    this.apply();
  };

  Diagram.prototype.bindNavigation = function () {
    var self = this;
    var canvas = this.canvas;
    var pointers = {};
    var drag = null;
    var moved = false;
    var toastTimer = null;

    function local(event) {
      var r = canvas.getBoundingClientRect();
      return { x: event.clientX - r.left, y: event.clientY - r.top };
    }

    canvas.addEventListener("wheel", function (event) {
      if (event.ctrlKey || event.metaKey || self.isFullscreen()) {
        event.preventDefault();
        var p = local(event);
        self.zoomAt(self.view.k * Math.exp(-event.deltaY * 0.0015), p.x, p.y);
        return;
      }
      self.toast.classList.add("rf-visible");
      clearTimeout(toastTimer);
      toastTimer = setTimeout(function () { self.toast.classList.remove("rf-visible"); }, 1200);
    }, { passive: false });

    canvas.addEventListener("pointerdown", function (event) {
      if (event.button !== 0) return;
      if (event.target.closest("a, .rf-body, .rf-toast")) return;
      pointers[event.pointerId] = local(event);
      moved = false;
      drag = { id: event.pointerId, start: local(event), view: { x: self.view.x, y: self.view.y } };
    });

    canvas.addEventListener("pointermove", function (event) {
      if (!(event.pointerId in pointers)) return;
      var p = local(event);
      pointers[event.pointerId] = p;
      var ids = Object.keys(pointers);
      if (ids.length === 2) {
        // Pinch: zoom around the midpoint by the change in finger spacing.
        var a = pointers[ids[0]], b = pointers[ids[1]];
        var dist = Math.hypot(a.x - b.x, a.y - b.y);
        if (self.pinch) self.zoomAt(self.view.k * dist / self.pinch, (a.x + b.x) / 2, (a.y + b.y) / 2);
        self.pinch = dist;
        moved = true;
        return;
      }
      if (!drag || drag.id !== event.pointerId) return;
      var dx = p.x - drag.start.x, dy = p.y - drag.start.y;
      if (!moved && Math.hypot(dx, dy) < 4) return;
      if (!moved) {
        moved = true;
        canvas.setPointerCapture(event.pointerId);
        canvas.classList.add("rf-dragging");
        self.highlight(null);
      }
      self.view.x = drag.view.x + dx;
      self.view.y = drag.view.y + dy;
      self.apply();
    });

    function release(event) {
      delete pointers[event.pointerId];
      self.pinch = null;
      if (drag && drag.id === event.pointerId) drag = null;
      canvas.classList.remove("rf-dragging");
    }
    canvas.addEventListener("pointerup", release);
    canvas.addEventListener("pointercancel", release);

    // A drag that started on a step must not also toggle it.
    canvas.addEventListener("click", function (event) {
      if (moved) {
        event.stopPropagation();
        event.preventDefault();
        moved = false;
      }
    }, true);

    canvas.addEventListener("keydown", function (event) {
      if (event.target !== canvas) return;
      var step = 60;
      var handled = true;
      switch (event.key) {
        case "+": case "=": self.zoomBy(1.25); break;
        case "-": case "_": self.zoomBy(1 / 1.25); break;
        case "0": self.fit(); break;
        case "ArrowLeft": self.pan(step, 0); break;
        case "ArrowRight": self.pan(-step, 0); break;
        case "ArrowUp": self.pan(0, step); break;
        case "ArrowDown": self.pan(0, -step); break;
        case "f": case "F": self.toggleFullscreen(); break;
        default: handled = false;
      }
      if (handled) event.preventDefault();
    });

    this.wrap.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && self.isFullscreen()) self.exitFullscreen();
    });
    document.addEventListener("fullscreenchange", function () {
      if (!document.fullscreenElement && self.isFullscreen()) self.exitFullscreen();
    });
  };

  // --- Full screen ---------------------------------------------------------

  Diagram.prototype.isFullscreen = function () {
    return this.wrap.classList.contains("rf-fullscreen");
  };

  Diagram.prototype.toggleFullscreen = function () {
    if (this.isFullscreen()) this.exitFullscreen(); else this.enterFullscreen();
  };

  // The diagram fills the window as an overlay, and also asks the browser for
  // real full screen where it can; leaving either leaves both.
  Diagram.prototype.enterFullscreen = function () {
    this.saved = { view: { k: this.view.k, x: this.view.x, y: this.view.y }, height: this.canvas.style.height };
    this.wrap.classList.add("rf-fullscreen");
    document.documentElement.classList.add("rf-no-scroll");
    this.canvas.style.height = "";
    this.setFullIcon("exitFullscreen", "Exit full screen (Esc)");
    if (this.wrap.requestFullscreen) {
      this.wrap.requestFullscreen().catch(function () {});
    }
    this.canvas.focus({ preventScroll: true });
    var self = this;
    requestAnimationFrame(function () { self.fit(); });
  };

  Diagram.prototype.exitFullscreen = function () {
    this.wrap.classList.remove("rf-fullscreen");
    document.documentElement.classList.remove("rf-no-scroll");
    this.setFullIcon("fullscreen", "Full screen (F)");
    if (document.fullscreenElement === this.wrap && document.exitFullscreen) {
      document.exitFullscreen().catch(function () {});
    }
    if (this.saved) {
      this.canvas.style.height = this.saved.height;
      this.view = this.saved.view;
      this.apply();
    }
  };

  Diagram.prototype.setFullIcon = function (name, title) {
    this.fullButton.replaceChild(icon(name), this.fullButton.firstChild);
    this.fullButton.title = title;
    this.fullButton.setAttribute("aria-label", title);
  };

  // --- Loading -------------------------------------------------------------

  function render(container) {
    if (container.dataset.rfState) return;
    container.dataset.rfState = "loading";
    if (typeof dagre === "undefined") {
      container.dataset.rfState = "failed";
      container.insertBefore(el("p", "rf-notice", "The diagram library did not load; the steps are listed below."), container.firstChild);
      return;
    }
    var url = siteRoot + "data/recipes/" + container.dataset.recipe + ".json";
    fetch(url)
      .then(function (response) {
        if (!response.ok) throw new Error(response.status + " " + url);
        return response.json();
      })
      .then(function (graph) {
        container.classList.add("rf-ready");
        var diagram = new Diagram(container, graph);
        container.diagram = diagram;
        diagrams.push(diagram);
        container.dataset.rfState = "done";
      })
      .catch(function (error) {
        container.dataset.rfState = "failed";
        container.classList.remove("rf-ready");
        container.insertBefore(el("p", "rf-notice", "The diagram could not be loaded (" + error.message + "); the steps are listed below."), container.firstChild);
      });
  }

  // Diagrams in a hidden tab cannot be measured, so each one is drawn the
  // first time it becomes visible.
  function init() {
    diagrams = diagrams.filter(function (d) { return document.contains(d.container); });
    var containers = document.querySelectorAll(".recipe-flow[data-recipe]");
    if (!("IntersectionObserver" in window)) {
      Array.prototype.forEach.call(containers, render);
      return;
    }
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          observer.unobserve(entry.target);
          render(entry.target);
        }
      });
    }, { rootMargin: "200px" });
    Array.prototype.forEach.call(containers, function (c) { observer.observe(c); });
  }

  if (typeof window.document$ !== "undefined" && window.document$.subscribe) {
    window.document$.subscribe(init);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

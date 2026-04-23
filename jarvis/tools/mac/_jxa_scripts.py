"""JXA (JavaScript for Automation) scripts embedded as strings."""

READ_UI_JXA = r"""
function run(argv) {
  const targetApp = argv[0] || "";
  const maxDepth = parseInt(argv[1] || "7", 10);
  const maxLines = parseInt(argv[2] || "400", 10);
  const se = Application("System Events");
  let appName = targetApp;
  if (!appName) {
    try { appName = se.processes.whose({frontmost: true})[0].name(); }
    catch(e) { return "ERROR: no frontmost app"; }
  }
  let proc;
  try { proc = se.processes.byName(appName); proc.name(); }
  catch(e) { return "ERROR: process '" + appName + "' not found: " + e.message; }

  const out = ["[UI of " + appName + "]"];
  const truncate = (s, n) => {
    s = String(s == null ? "" : s).replace(/\s+/g, " ").trim();
    return s.length > n ? s.slice(0, n) + "…" : s;
  };

  function walk(el, depth) {
    if (out.length >= maxLines) return;
    let children = [];
    try { children = el.uiElements(); } catch(e) {}
    for (const c of children) {
      if (out.length >= maxLines) return;
      let role = "?", nm = "", vl = "", ds = "", pos = "";
      try { role = c.role(); } catch(e) {}
      try { nm = c.name() || ""; } catch(e) {}
      try { const v = c.value(); if (v != null) vl = String(v); } catch(e) {}
      try { ds = c.description() || ""; } catch(e) {}
      try {
        const p = c.position(), s = c.size();
        if (p && s) pos = "@" + Math.round(p[0]+s[0]/2) + "," + Math.round(p[1]+s[1]/2);
      } catch(e) {}
      let line = "  ".repeat(depth) + "[" + role + "]";
      if (nm) line += ' name="' + truncate(nm, 80) + '"';
      if (vl && vl !== nm) line += ' value="' + truncate(vl, 120) + '"';
      if (ds && ds !== nm && ds !== vl) line += ' desc="' + truncate(ds, 80) + '"';
      if (pos) line += " " + pos;
      out.push(line);
      if (depth < maxDepth) walk(c, depth + 1);
    }
  }

  let wins = [];
  try { wins = proc.windows(); } catch(e) {}
  if (wins.length === 0) {
    out.push("(no windows — app may be launching or backgrounded)");
    try { walk(proc, 0); } catch(e) {}
  } else {
    for (let i = 0; i < wins.length; i++) {
      const w = wins[i];
      let title = ""; try { title = w.name() || ""; } catch(e) {}
      out.push('[Window ' + (i+1) + '] title="' + truncate(title, 100) + '"');
      walk(w, 1);
      if (out.length >= maxLines) { out.push("… [truncated at " + maxLines + " lines]"); break; }
    }
  }
  return out.join("\n");
}
"""

FIND_CLICK_JXA = r"""
function run(argv) {
  const appName = argv[0];
  const query = (argv[1] || "").toLowerCase();
  const roleFilter = (argv[2] || "").toLowerCase();
  const nth = parseInt(argv[3] || "1", 10);
  const se = Application("System Events");
  let proc;
  try { proc = se.processes.byName(appName); proc.name(); }
  catch(e) { return "ERROR: process not found"; }

  const hits = [];
  function walk(el, depth) {
    if (depth > 10 || hits.length >= nth + 5) return;
    let children = [];
    try { children = el.uiElements(); } catch(e) { return; }
    for (const c of children) {
      let role = "", nm = "", vl = "", ds = "";
      try { role = (c.role() || "").toLowerCase(); } catch(e) {}
      try { nm = (c.name() || "").toLowerCase(); } catch(e) {}
      try { const v = c.value(); if (v != null) vl = String(v).toLowerCase(); } catch(e) {}
      try { ds = (c.description() || "").toLowerCase(); } catch(e) {}
      const hay = nm + "\n" + vl + "\n" + ds;
      const roleOk = !roleFilter || role.indexOf(roleFilter) >= 0;
      if (roleOk && query && hay.indexOf(query) >= 0) hits.push(c);
      walk(c, depth + 1);
    }
  }
  try {
    const wins = proc.windows();
    if (wins.length) for (const w of wins) walk(w, 0); else walk(proc, 0);
  } catch(e) { return "ERROR walking: " + e.message; }

  if (hits.length < nth) return "NOT_FOUND (" + hits.length + " matches)";
  const target = hits[nth - 1];
  try {
    const p = target.position(), s = target.size();
    const cx = Math.round(p[0] + s[0]/2), cy = Math.round(p[1] + s[1]/2);
    // prefer AXPress action when available (works even if off-screen)
    try { target.actions.byName("AXPress").perform(); return "PRESSED at " + cx + "," + cy; }
    catch(e) {
      se.click({at: [cx, cy]});
      return "CLICKED at " + cx + "," + cy;
    }
  } catch(e) { return "ERROR clicking: " + e.message; }
}
"""

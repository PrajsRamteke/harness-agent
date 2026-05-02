"""Handle /project command — visual project graph HTML generator."""
import json
import os
import webbrowser
from pathlib import Path

from ..console import console
from ..tools.project_graph import ensure as ensure_graph, build, _get_root


def handle_project(c: str, arg: str) -> bool:
    """Handle /project subcommands.
    
    /project graph visual  — generate and open an interactive HTML project graph
    """
    if c != "/project":
        return False
    
    parts = arg.split()
    if len(parts) >= 2 and parts[0] == "graph" and parts[1] == "visual":
        _generate_visual()
        return True
    
    console.print("[yellow]usage: /project graph visual[/]")
    return True


def _generate_visual():
    """Build project graph and generate a standalone interactive HTML visualization."""
    root = _get_root()
    graph = ensure_graph(root, rebuild=False)
    
    if "error" in graph:
        console.print(f"[red]Error: {graph['error']}[/]")
        return
    
    html = _build_html(graph)
    
    out_path = root / ".project-graph-visual.html"
    out_path.write_text(html, encoding="utf-8")
    
    full = out_path.resolve()
    console.print(f"[green]✓ Project graph visual → {full}[/]")
    webbrowser.open(f"file://{full}")


def _build_html(graph: dict) -> str:
    """Generate a standalone interactive HTML page from the project graph."""
    name = graph.get("name", "Project")
    lang = graph.get("lang", "Unknown")
    fw = graph.get("fw", "unknown")
    total_files = graph.get("files", 0)
    scanned = graph.get("scanned", 0)
    entries = graph.get("entries", [])
    deps = graph.get("deps", {})
    dirs = graph.get("dirs", {})
    tree = graph.get("tree", {})
    
    # Serialize data as JSON for the JS side
    graph_json = json.dumps(graph, indent=2)
    
    # Build dep list string
    dep_items = json.dumps(list(deps.keys())[:30])
    dep_versions = json.dumps(dict(list(deps.items())[:30]))
    
    # Build file list with type info for the HTML
    all_files = []
    for parent in sorted(dirs):
        for fname in sorted(dirs.get(parent, [])):
            if not fname or "." not in fname or fname.endswith("/"):
                continue
            # Skip entries that are actually directories (have their own entry in dirs)
            clean = fname.rstrip("/")
            if clean in dirs:
                continue
            relpath = f"{parent}/{clean}" if parent else clean
            entry = tree.get(relpath, {})
            ext = Path(clean).suffix.lower()
            all_files.append({
                "path": relpath,
                "name": fname,
                "dir": parent,
                "ext": ext,
                "exports": entry.get("e", []),
                "imports": entry.get("i", []),
            })
    files_json = json.dumps(all_files)
    
    # File type color mapping
    ext_colors = {
        ".py": "#3572A5",
        ".ts": "#3178C6",
        ".tsx": "#3178C6",
        ".js": "#F7DF1E",
        ".jsx": "#F7DF1E",
        ".rs": "#DEA584",
        ".go": "#00ADD8",
        ".swift": "#F05138",
        ".kt": "#7F52FF",
        ".java": "#B07219",
        ".css": "#663399",
        ".scss": "#C6538C",
        ".html": "#E34F26",
        ".json": "#292929",
        ".yaml": "#CB171E",
        ".yml": "#CB171E",
        ".md": "#083FA1",
        ".toml": "#9C4221",
        ".lock": "#7E7E7E",
        ".txt": "#6e7681",
        ".cfg": "#8b949e",
        ".ini": "#8b949e",
        ".svg": "#FFB13B",
        ".xml": "#0060AC",
        ".sql": "#E38C00",
        ".sh": "#89E051",
        ".bash": "#89E051",
        ".zsh": "#89E051",
        ".yaml": "#CB171E",
        ".yml": "#CB171E",
        ".csv": "#217346",
        ".mdx": "#083FA1",
        ".vue": "#42B883",
        ".svelte": "#FF3E00",
        ".env": "#FFA500",
        ".gradle": "#02303A",
        ".kt": "#7F52FF",
        ".kts": "#7F52FF",
    }
    
    ext_colors_json = json.dumps(ext_colors)
    
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Project Graph — {name}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    background: #0d1117;
    color: #c9d1d9;
    overflow-x: hidden;
  }}
  ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
  ::-webkit-scrollbar-track {{ background: #161b22; }}
  ::-webkit-scrollbar-thumb {{ background: #30363d; border-radius: 4px; }}
  ::-webkit-scrollbar-thumb:hover {{ background: #484f58; }}

  .header {{
    background: linear-gradient(135deg, #161b22 0%, #0d1117 100%);
    border-bottom: 1px solid #30363d;
    padding: 24px 32px;
    position: sticky; top: 0; z-index: 100;
    backdrop-filter: blur(12px);
  }}
  .header-top {{
    display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 16px;
  }}
  .header h1 {{
    font-size: 24px; font-weight: 700;
    background: linear-gradient(90deg, #58a6ff, #79c0ff);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }}
  .header h1 span {{ font-weight: 400; opacity: 0.7; }}
  .header-badges {{ display: flex; gap: 8px; flex-wrap: wrap; }}
  .badge {{
    display: inline-flex; align-items: center; gap: 4px;
    padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 500;
    border: 1px solid #30363d;
  }}
  .badge-lang {{ background: #161b22; color: #58a6ff; }}
  .badge-fw {{ background: #161b22; color: #3fb950; }}
  .badge-files {{ background: #161b22; color: #d2a8ff; }}
  .badge-entry {{ background: #1f2a45; color: #79c0ff; border-color: #1f6feb; }}
  
  .stats-section {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px; padding: 20px 32px; background: #0d1117;
  }}
  .stat-card {{
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 16px; text-align: center;
    transition: border-color 0.2s, transform 0.15s;
  }}
  .stat-card:hover {{
    border-color: #58a6ff; transform: translateY(-2px);
  }}
  .stat-value {{
    font-size: 28px; font-weight: 700; color: #f0f6fc;
  }}
  .stat-label {{
    font-size: 12px; color: #8b949e; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px;
  }}
  .stat-icon {{ font-size: 20px; margin-bottom: 4px; }}

  .main {{ display: flex; height: calc(100vh - 180px); }}
  .sidebar {{
    width: 320px; min-width: 320px;
    border-right: 1px solid #30363d;
    display: flex; flex-direction: column;
    background: #0d1117;
  }}
  .sidebar-header {{
    padding: 12px 16px;
    border-bottom: 1px solid #30363d;
    font-size: 13px; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: 0.8px;
    display: flex; align-items: center; gap: 8px;
  }}
  .sidebar-header input {{
    flex: 1; background: #161b22; border: 1px solid #30363d;
    border-radius: 6px; padding: 6px 10px; color: #c9d1d9; font-size: 13px;
    outline: none; transition: border-color 0.15s;
  }}
  .sidebar-header input:focus {{ border-color: #58a6ff; }}
  .sidebar-header input::placeholder {{ color: #484f58; }}
  
  .tree-container {{ flex: 1; overflow-y: auto; padding: 8px 0; }}
  
  .tree-node {{
    font-size: 13px; line-height: 1.6;
    cursor: default; user-select: none;
  }}
  .tree-dir {{
    padding: 2px 0;
  }}
  .tree-dir-label {{
    display: flex; align-items: center; gap: 4px;
    padding: 2px 12px; cursor: pointer;
    color: #8b949e; font-weight: 500;
    transition: background 0.1s;
  }}
  .tree-dir-label:hover {{ background: #161b22; }}
  .tree-dir-label .arrow {{
    display: inline-block; width: 12px; text-align: center;
    transition: transform 0.15s; font-size: 10px; color: #484f58;
  }}
  .tree-dir-label .arrow.open {{ transform: rotate(90deg); }}
  .tree-dir-label .dir-icon {{ color: #d29922; font-size: 14px; }}
  .tree-dir-children {{ display: none; padding-left: 20px; }}
  .tree-dir-children.open {{ display: block; }}
  
  .tree-file {{
    display: flex; align-items: center; gap: 6px;
    padding: 2px 12px 2px 32px; cursor: pointer;
    transition: background 0.1s;
    color: #c9d1d9;
  }}
  .tree-file:hover {{ background: #161b22; }}
  .tree-file .ext-dot {{
    width: 8px; height: 8px; border-radius: 2px; display: inline-block; flex-shrink: 0;
  }}
  .tree-file .file-name {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .tree-file .file-imports {{
    margin-left: auto; font-size: 10px; color: #484f58;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 80px;
  }}
  .tree-file.selected {{ background: #1f2a45; }}

  .content {{
    flex: 1; display: flex; flex-direction: column;
    overflow: hidden; background: #0d1117;
  }}
  .content-header {{
    padding: 12px 20px;
    border-bottom: 1px solid #30363d;
    font-size: 14px; font-weight: 600; color: #f0f6fc;
    display: flex; align-items: center; gap: 8px;
  }}
  .content-header .file-path {{ color: #8b949e; font-weight: 400; font-size: 13px; }}
  .content-body {{
    flex: 1; overflow-y: auto; padding: 20px;
  }}
  .empty-state {{
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    height: 100%; color: #484f58; gap: 16px;
  }}
  .empty-state .big-icon {{ font-size: 48px; opacity: 0.5; }}
  .empty-state p {{ font-size: 14px; }}

  .file-detail-card {{
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 20px; margin-bottom: 16px;
  }}
  .file-detail-card h3 {{
    font-size: 16px; color: #f0f6fc; margin-bottom: 8px;
  }}
  .file-detail-card .meta {{
    display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px;
  }}
  .file-detail-card .meta-item {{
    font-size: 12px; color: #8b949e;
  }}
  .file-detail-card .meta-item strong {{ color: #c9d1d9; }}
  
  .tag-list {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .tag {{
    display: inline-flex; align-items: center; gap: 4px;
    padding: 2px 10px; border-radius: 12px; font-size: 12px;
    border: 1px solid #30363d;
  }}
  .tag-export {{ background: #1a3a2a; color: #3fb950; border-color: #238636; }}
  .tag-import {{ background: #1f2a45; color: #79c0ff; border-color: #1f6feb; }}

  .dep-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 8px; margin-top: 8px;
  }}
  .dep-card {{
    background: #161b22; border: 1px solid #30363d; border-radius: 6px;
    padding: 10px 14px; font-size: 13px;
  }}
  .dep-card .dep-name {{ color: #58a6ff; font-weight: 500; }}
  .dep-card .dep-ver {{ color: #8b949e; font-size: 11px; margin-left: 6px; }}

  .graph-canvas-wrap {{
    width: 100%; height: 400px; background: #0d1117;
    border: 1px solid #30363d; border-radius: 8px; overflow: hidden;
    position: relative;
  }}
  .graph-canvas-wrap canvas {{ width: 100% !important; height: 100% !important; }}

  .filter-bar {{
    display: flex; gap: 8px; padding: 12px 20px;
    border-bottom: 1px solid #30363d; flex-wrap: wrap;
  }}
  .filter-btn {{
    padding: 4px 12px; border-radius: 16px; font-size: 12px;
    border: 1px solid #30363d; background: transparent; color: #8b949e;
    cursor: pointer; transition: all 0.15s;
  }}
  .filter-btn:hover {{ border-color: #58a6ff; color: #58a6ff; }}
  .filter-btn.active {{ background: #1f6feb; border-color: #1f6feb; color: #fff; }}

  .entry-points {{
    padding: 12px 20px; border-bottom: 1px solid #30363d;
    display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
  }}
  .entry-points-label {{ font-size: 12px; color: #8b949e; font-weight: 600; }}
  .entry-badge {{
    padding: 2px 10px; border-radius: 12px; font-size: 11px;
    background: #1f2a45; color: #79c0ff; border: 1px solid #1f6feb;
  }}

  @media (max-width: 768px) {{
    .main {{ flex-direction: column; height: auto; }}
    .sidebar {{ width: 100%; min-width: auto; max-height: 300px; border-right: none; border-bottom: 1px solid #30363d; }}
    .header {{ padding: 16px; }}
    .stats-section {{ padding: 12px 16px; }}
    .stats-section {{ grid-template-columns: repeat(2, 1fr); }}
  }}

  .highlight {{ background: #3a1e1e !important; color: #ff7b72 !important; }}
  .fade {{ opacity: 0.25; }}
  
  .progress-bar {{
    width: 100%; height: 3px; background: #161b22; border-radius: 2px; overflow: hidden;
    margin-top: 4px;
  }}
  .progress-fill {{
    height: 100%; border-radius: 2px; transition: width 0.3s;
    background: linear-gradient(90deg, #58a6ff, #79c0ff);
  }}
</style>
</head>
<body>

<div class="header">
  <div class="header-top">
    <h1>📁 {name} <span>— Project Graph</span></h1>
    <div class="header-badges">
      <span class="badge badge-lang">⬡ {lang}</span>
      <span class="badge badge-fw">◆ {fw}</span>
      <span class="badge badge-files">⊞ {scanned} src / {total_files} total</span>
      {''.join(f'<span class="badge badge-entry">★ {e}</span>' for e in entries[:3])}
    </div>
  </div>
</div>

<div class="stats-section">
  <div class="stat-card">
    <div class="stat-icon">📄</div>
    <div class="stat-value">{scanned}</div>
    <div class="stat-label">Source Files</div>
  </div>
  <div class="stat-card">
    <div class="stat-icon">📂</div>
    <div class="stat-value">{len([p for p in dirs if p])}</div>
    <div class="stat-label">Directories</div>
  </div>
  <div class="stat-card">
    <div class="stat-icon">🔗</div>
    <div class="stat-value">{len(deps)}</div>
    <div class="stat-label">Dependencies</div>
  </div>
  <div class="stat-card">
    <div class="stat-icon">📤</div>
    <div class="stat-value">{sum(1 for f in all_files if f['exports'])}</div>
    <div class="stat-label">Files with Exports</div>
  </div>
  <div class="stat-card">
    <div class="stat-icon">📥</div>
    <div class="stat-value">{sum(1 for f in all_files if f['imports'])}</div>
    <div class="stat-label">Files with Imports</div>
  </div>
  <div class="stat-card">
    <div class="stat-icon">🎯</div>
    <div class="stat-value">{len(entries)}</div>
    <div class="stat-label">Entry Points</div>
  </div>
</div>

<div class="main">
  <div class="sidebar">
    <div class="sidebar-header">
      🔍 <input type="text" id="searchInput" placeholder="Search files..." oninput="filterTree()">
    </div>
    <div class="tree-container" id="treeContainer"></div>
  </div>
  <div class="content">
    <div class="content-header" id="contentHeader">
      <span>📄 File Details</span>
      <span class="file-path" id="filePath"></span>
    </div>
    <div class="filter-bar" id="filterBar">
      <button class="filter-btn active" data-ext="all" onclick="setFilter('all', this)">All</button>
      <button class="filter-btn" data-ext=".py" onclick="setFilter('.py', this)">Python</button>
      <button class="filter-btn" data-ext=".ts,.tsx" onclick="setFilter('.ts,.tsx', this)">TypeScript</button>
      <button class="filter-btn" data-ext=".js,.jsx" onclick="setFilter('.js,.jsx', this)">JavaScript</button>
      <button class="filter-btn" data-ext=".rs" onclick="setFilter('.rs', this)">Rust</button>
      <button class="filter-btn" data-ext="other" onclick="setFilter('other', this)">Other</button>
    </div>
    {f'''<div class="entry-points">
      <span class="entry-points-label">★ Entry Points:</span>
      {"".join(f'<span class="entry-badge">{e}</span>' for e in entries[:10])}
    </div>''' if entries else ''}
    <div class="content-body" id="contentBody">
      <div class="empty-state">
        <div class="big-icon">🗺️</div>
        <p>Click a file in the tree to inspect its details</p>
        <p style="font-size:12px;color:#30363d">{scanned} source files · {len(all_files)} tracked</p>
      </div>
    </div>
  </div>
</div>

<script>
const files = {files_json};
const graph = {graph_json};
const extColors = {ext_colors_json};

// Build tree
function buildTree(data) {{
  const tree = {{ __children: {{}} }};
  for (const f of data) {{
    const parts = f.dir ? f.dir.split('/') : [];
    let node = tree;
    for (const p of parts) {{
      if (!node.__children[p]) node.__children[p] = {{ __children: {{}} }};
      node = node.__children[p];
    }}
    node.__children[f.name] = {{ __file: true, ...f }};
  }}
  return tree;
}}

function getExtColor(ext) {{
  return extColors[ext] || '#6e7681';
}}

function renderTreeNode(name, node, depth, searchTerm) {{
  if (node.__file) {{
    const ext = node.ext || '';
    const color = getExtColor(ext);
    const match = searchTerm && node.path.toLowerCase().includes(searchTerm.toLowerCase());
    const cls = match ? 'highlight' : '';
    return `<div class="tree-file ${{cls}}" onclick="selectFile('${{node.path.replace(/'/g, "\\\\'")}}', this)">
      <span class="ext-dot" style="background:${{color}}"></span>
      <span class="file-name">${{node.name}}</span>
      <span class="file-imports">${{node.imports.length ? '↩' + node.imports.length : ''}}</span>
    </div>`;
  }}
  
  const children = Object.keys(node.__children || {{}});
  const dirs = children.filter(k => !node.__children[k].__file);
  const files = children.filter(k => node.__children[k].__file);
  
  if (searchTerm) {{
    // Check if any file matches in subtree
    const hasMatch = files.some(f => node.__children[f].path.toLowerCase().includes(searchTerm.toLowerCase()))
      || dirs.some(d => renderSubtreeHasMatch(node.__children[d], searchTerm));
    if (!hasMatch) return '';
  }}
  
  const fileHtml = files.map(f => renderTreeNode(f, node.__children[f], depth + 1, searchTerm)).join('');
  const dirHtml = dirs.map(d => renderTreeNode(d, node.__children[d], depth + 1, searchTerm)).join('');
  const childrenHtml = fileHtml + dirHtml;
  
  if (!childrenHtml && !searchTerm) return '';
  if (!childrenHtml) return '';
  
  return `<div class="tree-dir">
    <div class="tree-dir-label" onclick="toggleDir(this)">
      <span class="arrow open">▶</span>
      <span class="dir-icon">📁</span>
      <span>${{name || '/'}}</span>
    </div>
    <div class="tree-dir-children open">${{childrenHtml}}</div>
  </div>`;
}}

function renderSubtreeHasMatch(node, searchTerm) {{
  if (node.__file) return node.path.toLowerCase().includes(searchTerm.toLowerCase());
  const children = Object.keys(node.__children || {{}});
  return children.some(k => renderSubtreeHasMatch(node.__children[k], searchTerm));
}}

function toggleDir(el) {{
  const arrow = el.querySelector('.arrow');
  const children = el.nextElementSibling;
  arrow.classList.toggle('open');
  children.classList.toggle('open');
}}

const treeData = buildTree(files);

function renderTree() {{
  const searchTerm = document.getElementById('searchInput').value;
  const container = document.getElementById('treeContainer');
  container.innerHTML = renderTreeNode('', treeData, 0, searchTerm);
}}

renderTree();

function filterTree() {{
  renderTree();
}}

let selectedFile = null;
let currentFilter = 'all';

function setFilter(ext, btn) {{
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  currentFilter = ext;
  if (selectedFile) {{
    selectFileByPath(selectedFile);
  }}
}}

function selectFile(path, el) {{
  document.querySelectorAll('.tree-file').forEach(e => e.classList.remove('selected'));
  if (el) el.classList.add('selected');
  selectedFile = path;
  selectFileByPath(path);
}}

function selectFileByPath(path) {{
  const file = files.find(f => f.path === path);
  if (!file) return;
  
  const body = document.getElementById('contentBody');
  const pathEl = document.getElementById('filePath');
  pathEl.textContent = path;
  
  const ext = file.ext || '';
  const color = getExtColor(ext);
  const relDir = file.dir || '/';
  
  let importsHtml = '';
  if (file.imports && file.imports.length) {{
    importsHtml = `<div class="file-detail-card">
      <h3>📥 Imports</h3>
      <div class="tag-list">${{file.imports.map(i => `<span class="tag tag-import">${{i}}</span>`).join('')}}</div>
    </div>`;
  }}
  
  let exportsHtml = '';
  if (file.exports && file.exports.length) {{
    exportsHtml = `<div class="file-detail-card">
      <h3>📤 Exports</h3>
      <div class="tag-list">${{file.exports.map(e => `<span class="tag tag-export">${{e}}</span>`).join('')}}</div>
    </div>`;
  }}
  
  body.innerHTML = `<div class="file-detail-card">
    <h3>${{file.name}}</h3>
    <div class="meta">
      <span class="meta-item">📁 <strong>${{relDir}}</strong></span>
      <span class="meta-item">🔤 <strong>${{ext || 'none'}}</strong></span>
      <span class="meta-item">↩ <strong>${{file.imports.length}}</strong> imports</span>
      <span class="meta-item">↪ <strong>${{file.exports.length}}</strong> exports</span>
    </div>
    <div class="progress-bar">
      <div class="progress-fill" style="width:${{Math.min(100, file.exports.length * 25 + file.imports.length * 10)}}%"></div>
    </div>
  </div>
  ${{importsHtml}}
  ${{exportsHtml}}
  <div class="file-detail-card">
    <h3>🔗 Connected Files</h3>
    <div class="tag-list">
      ${{findConnectedFiles(file).map(c => `<span class="tag" style="cursor:pointer" onclick="selectFileByPath('${{c.path.replace(/'/g, "\\\\'")}}')">${{c.path}}</span>`).join('') || '<span style="color:#484f58">No direct connections</span>'}}
    </div>
  </div>`;
}}

function findConnectedFiles(file) {{
  const connected = [];
  for (const f of files) {{
    if (f.path === file.path) continue;
    const myImports = file.imports || [];
    const theirImports = f.imports || [];
    const myExports = file.exports || [];
    const theirExports = f.exports || [];
    
    // f imports something I export
    for (const e of myExports) {{
      if (theirImports.some(i => i.includes(e) || e.includes(i))) {{
        connected.push(f); break;
      }}
    }}
    if (connected.length > 0 && connected[connected.length-1] === f) continue;
    // I import something f exports
    for (const e of theirExports) {{
      if (myImports.some(i => i.includes(e) || e.includes(i))) {{
        connected.push(f); break;
      }}
    }}
  }}
  return connected.slice(0, 12);
}}

// Dependency visualization
{dep_html if False else ''}
</script>

<script>
// Additional: dependency cards at bottom
(function() {{
  const body = document.getElementById('contentBody');
  const empty = body.querySelector('.empty-state');
  if (empty) {{
    empty.innerHTML = `
      <div style="text-align:center">
        <div class="big-icon" style="font-size:64px">🗺️</div>
        <p style="font-size:18px;color:#f0f6fc;margin:12px 0 4px">Project Graph Active</p>
        <p style="font-size:13px;color:#8b949e">{scanned} source files across {len(dirs)} directories</p>
        <p style="font-size:12px;color:#484f58;margin-top:16px">Click any file in the sidebar tree to inspect details →</p>
      </div>
    `;
  }}
}})();
</script>

</body>
</html>'''

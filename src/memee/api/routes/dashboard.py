"""Dashboard route — serves the HTML dashboard."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse)
def dashboard():
    """Serve the Memee dashboard."""
    return DASHBOARD_HTML


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Memee — Organizational Learning Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            background: #0a0a0f;
            color: #e0e0e0;
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            padding: 24px 32px;
            border-bottom: 1px solid #2a2a4a;
        }
        .header h1 {
            font-size: 28px;
            font-weight: 700;
            background: linear-gradient(90deg, #00d2ff, #7b2ff7);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .header .tagline {
            color: #888;
            font-size: 14px;
            margin-top: 4px;
        }
        .org-iq-banner {
            display: flex;
            align-items: center;
            gap: 24px;
            background: linear-gradient(135deg, #1e1e3f 0%, #2d1b69 100%);
            padding: 24px 32px;
            border-bottom: 1px solid #3a2a6a;
        }
        .org-iq-score {
            font-size: 64px;
            font-weight: 800;
            background: linear-gradient(90deg, #ff6b6b, #ffd93d, #6bcb77, #4d96ff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .org-iq-label { color: #aaa; font-size: 14px; }
        .org-iq-details { flex: 1; }
        .org-iq-bar {
            height: 8px;
            background: #2a2a4a;
            border-radius: 4px;
            margin-top: 8px;
            overflow: hidden;
        }
        .org-iq-fill {
            height: 100%;
            border-radius: 4px;
            background: linear-gradient(90deg, #ff6b6b, #ffd93d, #6bcb77, #4d96ff);
            transition: width 1s ease;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 20px;
            padding: 24px 32px;
        }
        .card {
            background: #12121f;
            border: 1px solid #2a2a4a;
            border-radius: 12px;
            padding: 20px;
        }
        .card h2 {
            font-size: 16px;
            font-weight: 600;
            color: #aaa;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 16px;
        }
        .stat-row {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #1a1a2e;
        }
        .stat-label { color: #888; }
        .stat-value { font-weight: 600; color: #fff; }
        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
        }
        .badge-canon { background: #1a472a; color: #6bcb77; }
        .badge-validated { background: #1a3a5c; color: #4d96ff; }
        .badge-tested { background: #3a3a1a; color: #ffd93d; }
        .badge-hypothesis { background: #2a2a2a; color: #888; }
        .badge-deprecated { background: #3a1a1a; color: #ff6b6b; }
        .badge-critical { background: #5c1a1a; color: #ff4444; }
        .badge-high { background: #5c3a1a; color: #ff8844; }
        .badge-medium { background: #3a3a1a; color: #ffd93d; }
        .badge-low { background: #1a3a2a; color: #6bcb77; }
        .chart-container {
            position: relative;
            height: 250px;
        }
        .memory-list {
            max-height: 350px;
            overflow-y: auto;
        }
        .memory-item {
            padding: 10px 0;
            border-bottom: 1px solid #1a1a2e;
        }
        .memory-title {
            font-weight: 500;
            color: #ddd;
            font-size: 13px;
        }
        .memory-meta {
            font-size: 11px;
            color: #666;
            margin-top: 4px;
        }
        .agent-row {
            display: flex;
            align-items: center;
            padding: 8px 0;
            border-bottom: 1px solid #1a1a2e;
            gap: 12px;
        }
        .agent-name {
            font-weight: 600;
            width: 100px;
            color: #ddd;
        }
        .agent-bar {
            flex: 1;
            height: 20px;
            background: #1a1a2e;
            border-radius: 4px;
            overflow: hidden;
        }
        .agent-fill {
            height: 100%;
            border-radius: 4px;
            transition: width 0.5s ease;
        }
        .agent-score {
            width: 50px;
            text-align: right;
            font-weight: 600;
        }
        .tag {
            display: inline-block;
            padding: 1px 6px;
            background: #1a1a3e;
            border-radius: 3px;
            font-size: 10px;
            color: #7b8ec8;
            margin: 1px;
        }
        .full-width { grid-column: 1 / -1; }
        .loading { color: #555; text-align: center; padding: 40px; }
        @media (max-width: 800px) {
            .grid { grid-template-columns: 1fr; padding: 12px; }
            .header, .org-iq-banner { padding: 16px; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Memee</h1>
        <div class="tagline">Your agents forget. Memee doesn't.</div>
    </div>

    <div class="org-iq-banner" id="iq-banner">
        <div>
            <div class="org-iq-label">Organizational IQ</div>
            <div class="org-iq-score" id="org-iq-score">—</div>
        </div>
        <div class="org-iq-details">
            <div id="iq-breakdown" style="font-size:13px; color:#888;"></div>
            <div class="org-iq-bar">
                <div class="org-iq-fill" id="iq-bar-fill" style="width: 0%"></div>
            </div>
        </div>
    </div>

    <div class="grid">
        <!-- Retrieval Health (hit@1, hit@3, acceptance, p50 latency) -->
        <div class="card full-width" id="retrieval-card">
            <h2>Retrieval Health</h2>
            <div id="retrieval-cards" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;">
                <div class="loading">Loading...</div>
            </div>
            <div class="chart-container" style="height:120px;margin-top:16px;"><canvas id="hit1Sparkline"></canvas></div>
            <div style="font-size:10px;color:#555;margin-top:8px;">
                7-day window. Sparkline: daily hit@1 over 30 days.
                <em>p50 time-to-solution is a proxy (search latency for accepted events).</em>
            </div>
        </div>

        <!-- Impact (honest AP accounting: shown / acknowledged / avoided) -->
        <div class="card full-width" id="impact-card">
            <h2>Impact</h2>
            <div id="impact-cards" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;">
                <div class="loading">Loading...</div>
            </div>
        </div>

        <!-- Stats Cards -->
        <div class="card" id="stats-card">
            <h2>Overview</h2>
            <div class="loading">Loading...</div>
        </div>

        <!-- Maturity Distribution -->
        <div class="card">
            <h2>Maturity Distribution</h2>
            <div class="chart-container"><canvas id="maturityChart"></canvas></div>
        </div>

        <!-- Confidence Distribution -->
        <div class="card">
            <h2>Confidence Distribution</h2>
            <div class="chart-container"><canvas id="confidenceChart"></canvas></div>
        </div>

        <!-- Memory Types -->
        <div class="card">
            <h2>Memory Types</h2>
            <div class="chart-container"><canvas id="typesChart"></canvas></div>
        </div>

        <!-- Agent Leaderboard -->
        <div class="card">
            <h2>Agent Leaderboard</h2>
            <div id="agent-list" class="memory-list"><div class="loading">Loading...</div></div>
        </div>

        <!-- Anti-Patterns -->
        <div class="card">
            <h2>Anti-Pattern Library</h2>
            <div id="ap-list" class="memory-list"><div class="loading">Loading...</div></div>
        </div>

        <!-- Top Memories -->
        <div class="card full-width">
            <h2>Highest Confidence Memories</h2>
            <div id="memory-list" class="memory-list"><div class="loading">Loading...</div></div>
        </div>

        <!-- Projects -->
        <div class="card full-width">
            <h2>Projects</h2>
            <div id="project-list" class="memory-list"><div class="loading">Loading...</div></div>
        </div>
    </div>

    <script>
    const API = '/api/v1';
    const COLORS = {
        canon: '#6bcb77', validated: '#4d96ff', tested: '#ffd93d',
        hypothesis: '#888888', deprecated: '#ff6b6b',
        pattern: '#4d96ff', anti_pattern: '#ff6b6b', decision: '#ffd93d',
        lesson: '#6bcb77', observation: '#888888',
    };

    // XSS guard — every user-controlled string (memory title/tags,
    // anti-pattern trigger/alternative, agent name, project name) goes
    // through escapeHTML before being interpolated into innerHTML. Server-
    // controlled enums (maturity, severity, memory type) are whitelisted
    // in COLORS/class names so they do not need escaping.
    function escapeHTML(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
            ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])
        );
    }

    async function fetchJSON(path) {
        const res = await fetch(API + path);
        return res.json();
    }

    // ── Stats Card ──
    async function loadStats() {
        const data = await fetchJSON('/stats');
        if (data.empty) {
            document.getElementById('stats-card').innerHTML =
                '<h2>Overview</h2><div class="loading">No data yet. Run a simulation first.</div>';
            return;
        }

        document.getElementById('org-iq-score').textContent = data.org_iq;
        document.getElementById('iq-bar-fill').style.width = data.org_iq + '%';
        document.getElementById('iq-breakdown').textContent =
            `${data.total_memories} memories · ${data.projects} projects · ${data.connections} connections · avg conf ${data.avg_confidence}`;

        const mat = data.maturity || {};
        document.getElementById('stats-card').innerHTML = `
            <h2>Overview</h2>
            <div class="stat-row"><span class="stat-label">Total Memories</span><span class="stat-value">${data.total_memories}</span></div>
            <div class="stat-row"><span class="stat-label">Projects</span><span class="stat-value">${data.projects}</span></div>
            <div class="stat-row"><span class="stat-label">Graph Connections</span><span class="stat-value">${data.connections}</span></div>
            <div class="stat-row"><span class="stat-label">Avg Confidence</span><span class="stat-value">${(data.avg_confidence * 100).toFixed(1)}%</span></div>
            <div class="stat-row"><span class="stat-label">Canon</span><span class="stat-value"><span class="badge badge-canon">${mat.canon || 0}</span></span></div>
            <div class="stat-row"><span class="stat-label">Validated</span><span class="stat-value"><span class="badge badge-validated">${mat.validated || 0}</span></span></div>
            <div class="stat-row"><span class="stat-label">Tested</span><span class="stat-value"><span class="badge badge-tested">${mat.tested || 0}</span></span></div>
            <div class="stat-row"><span class="stat-label">Hypothesis</span><span class="stat-value"><span class="badge badge-hypothesis">${mat.hypothesis || 0}</span></span></div>
            <div class="stat-row"><span class="stat-label">Deprecated</span><span class="stat-value"><span class="badge badge-deprecated">${mat.deprecated || 0}</span></span></div>
        `;

        // Maturity chart
        new Chart(document.getElementById('maturityChart'), {
            type: 'doughnut',
            data: {
                labels: ['Canon', 'Validated', 'Tested', 'Hypothesis', 'Deprecated'],
                datasets: [{
                    data: [mat.canon||0, mat.validated||0, mat.tested||0, mat.hypothesis||0, mat.deprecated||0],
                    backgroundColor: [COLORS.canon, COLORS.validated, COLORS.tested, COLORS.hypothesis, COLORS.deprecated],
                    borderWidth: 0,
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { position: 'bottom', labels: { color: '#888', font: { size: 11 } } } }
            }
        });

        // Types chart
        const types = data.types || {};
        new Chart(document.getElementById('typesChart'), {
            type: 'bar',
            data: {
                labels: Object.keys(types),
                datasets: [{
                    data: Object.values(types),
                    backgroundColor: Object.keys(types).map(t => COLORS[t] || '#555'),
                    borderWidth: 0, borderRadius: 4,
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false, indexAxis: 'y',
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#666' }, grid: { color: '#1a1a2e' } },
                    y: { ticks: { color: '#aaa' }, grid: { display: false } },
                }
            }
        });
    }

    // ── Confidence Distribution ──
    async function loadConfidence() {
        const data = await fetchJSON('/confidence-distribution');
        new Chart(document.getElementById('confidenceChart'), {
            type: 'bar',
            data: {
                labels: Object.keys(data),
                datasets: [{
                    data: Object.values(data),
                    backgroundColor: Object.keys(data).map(k => {
                        const v = parseFloat(k);
                        if (v >= 0.8) return COLORS.canon;
                        if (v >= 0.6) return COLORS.validated;
                        if (v >= 0.4) return COLORS.tested;
                        return COLORS.hypothesis;
                    }),
                    borderWidth: 0, borderRadius: 4,
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#666' }, grid: { color: '#1a1a2e' } },
                    y: { ticks: { color: '#666' }, grid: { color: '#1a1a2e' } },
                }
            }
        });
    }

    // ── Agent Leaderboard ──
    async function loadAgents() {
        const agents = await fetchJSON('/agents');
        const el = document.getElementById('agent-list');
        if (!agents.length) { el.innerHTML = '<div class="loading">No agent data.</div>'; return; }
        const maxMem = Math.max(...agents.map(a => a.memories));
        el.innerHTML = agents.map(a => {
            const pct = (a.avg_confidence * 100).toFixed(0);
            const color = a.avg_confidence > 0.7 ? COLORS.canon : a.avg_confidence > 0.5 ? COLORS.validated : COLORS.hypothesis;
            return `<div class="agent-row">
                <span class="agent-name">${escapeHTML(a.name)}</span>
                <div class="agent-bar"><div class="agent-fill" style="width:${a.memories/maxMem*100}%;background:${color}"></div></div>
                <span class="agent-score" style="color:${color}">${pct}%</span>
                <span style="font-size:11px;color:#555;width:60px;text-align:right">${a.memories} mem</span>
            </div>`;
        }).join('');
    }

    // ── Anti-Patterns ──
    async function loadAntiPatterns() {
        const aps = await fetchJSON('/anti-patterns');
        const el = document.getElementById('ap-list');
        if (!aps.length) { el.innerHTML = '<div class="loading">No anti-patterns yet.</div>'; return; }
        el.innerHTML = aps.map(a => `
            <div class="memory-item">
                <div class="memory-title">
                    <span class="badge badge-${escapeHTML(a.severity)}">${escapeHTML(a.severity)}</span>
                    ${escapeHTML(a.title)}
                </div>
                <div class="memory-meta">
                    Trigger: ${escapeHTML(a.trigger)}<br>
                    Alternative: ${a.alternative ? escapeHTML(a.alternative) : '—'}
                </div>
                <div class="memory-meta">${(a.tags||[]).map(t => '<span class="tag">'+escapeHTML(t)+'</span>').join(' ')}</div>
            </div>
        `).join('');
    }

    // ── Memory List ──
    async function loadMemories() {
        const mems = await fetchJSON('/memories?limit=20');
        const el = document.getElementById('memory-list');
        if (!mems.length) { el.innerHTML = '<div class="loading">No memories yet.</div>'; return; }
        el.innerHTML = mems.map(m => `
            <div class="memory-item">
                <div class="memory-title">
                    <span class="badge badge-${escapeHTML(m.maturity)}">${escapeHTML(m.maturity)}</span>
                    ${escapeHTML(m.title)}
                </div>
                <div class="memory-meta">
                    ${escapeHTML(m.type)} · conf: ${(m.confidence*100).toFixed(0)}% · ${m.validations} validations · ${m.projects} projects
                    ${m.agent ? ' · by ' + escapeHTML(m.agent) : ''}
                </div>
                <div class="memory-meta">${(m.tags||[]).map(t => '<span class="tag">'+escapeHTML(t)+'</span>').join(' ')}</div>
            </div>
        `).join('');
    }

    // ── Projects ──
    async function loadProjects() {
        const projs = await fetchJSON('/projects');
        const el = document.getElementById('project-list');
        if (!projs.length) { el.innerHTML = '<div class="loading">No projects.</div>'; return; }
        const maxMem = Math.max(...projs.map(p => p.memories));
        el.innerHTML = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px">' +
            projs.map(p => {
                const pct = maxMem > 0 ? (p.memories / maxMem * 100) : 0;
                return `<div style="background:#1a1a2e;border-radius:8px;padding:12px">
                    <div style="font-weight:600;color:#ddd">${escapeHTML(p.name)}</div>
                    <div style="font-size:11px;color:#666;margin:4px 0">${(p.stack||[]).map(s => escapeHTML(s)).join(' · ')}</div>
                    <div style="height:6px;background:#0a0a0f;border-radius:3px;margin-top:6px">
                        <div style="height:100%;width:${pct}%;background:${COLORS.validated};border-radius:3px"></div>
                    </div>
                    <div style="font-size:11px;color:#888;margin-top:4px">${p.memories} memories</div>
                </div>`;
            }).join('') + '</div>';
    }

    // ── Retrieval Health ──
    function smallCard(label, value, footer) {
        const foot = footer ? `<div style="font-size:10px;color:#555;margin-top:6px;">${footer}</div>` : '';
        return `<div style="background:#1a1a2e;border-radius:8px;padding:12px;">
            <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;">${label}</div>
            <div style="font-size:28px;font-weight:700;color:#fff;margin-top:4px;">${value}</div>
            ${foot}
        </div>`;
    }

    async function loadRetrieval() {
        const data = await fetchJSON('/retrieval');
        const w = (data.windows && data.windows.day_7) || {
            hit_at_1: 0, hit_at_3: 0, accepted_memory_rate: 0,
            time_to_solution_p50_ms: 0, total: 0, accepted: 0,
        };
        const pct = (x) => (x * 100).toFixed(1) + '%';
        const el = document.getElementById('retrieval-cards');
        el.innerHTML = [
            smallCard('hit@1', pct(w.hit_at_1), `${w.accepted}/${w.total} accepted`),
            smallCard('hit@3', pct(w.hit_at_3), 'position < 3'),
            smallCard('accepted rate', pct(w.accepted_memory_rate), 'of all searches'),
            smallCard('p50 time-to-solution', (w.time_to_solution_p50_ms || 0).toFixed(0) + ' ms',
                'proxy: search latency'),
        ].join('');

        const spark = data.hit_at_1_sparkline_30d || [];
        new Chart(document.getElementById('hit1Sparkline'), {
            type: 'line',
            data: {
                labels: spark.map(s => s.date.slice(5)),
                datasets: [{
                    data: spark.map(s => s.hit_at_1),
                    borderColor: COLORS.validated,
                    backgroundColor: 'rgba(77,150,255,0.15)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0,
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#555', maxTicksLimit: 6 }, grid: { display: false } },
                    y: { min: 0, max: 1, ticks: { color: '#555', stepSize: 0.5 }, grid: { color: '#1a1a2e' } },
                }
            }
        });
    }

    // ── Impact (honest split: shown / acknowledged / avoided) ──
    async function loadImpact() {
        const data = await fetchJSON('/impact');
        const shown = data.warnings_shown || 0;
        const ack = data.warnings_acknowledged || 0;
        const avoided = data.mistakes_avoided || 0;
        const el = document.getElementById('impact-cards');
        el.innerHTML = [
            smallCard('warnings shown', shown,
                'anti-pattern links delivered'),
            smallCard('acknowledged', ack,
                'agent recorded an outcome'),
            smallCard('avoided (evidence-backed)', avoided,
                'must have diff, test, review, PR, or agent-feedback ref'),
        ].join('');
    }

    // ── Init ──
    Promise.all([
        loadStats(), loadConfidence(), loadAgents(), loadAntiPatterns(),
        loadMemories(), loadProjects(), loadRetrieval(), loadImpact(),
    ]);
    </script>
</body>
</html>"""

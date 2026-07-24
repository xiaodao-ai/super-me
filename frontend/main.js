// ─── 超级个体 · 3D 入口 ───────────────────────────────────
// WebSocket 拉取世界快照(10Hz) → 插值平滑 → WebGPU 3D 渲染
import { Renderer3D } from "./renderer3d.js";
import { buildOffice, buildCharacter, buildLink, buildScreen,
         X, Z, WALL_Z } from "./scene3d.js";
import { UI } from "./ui.js";

const canvas = document.getElementById("gpu");
const ui = new UI((id) => selectAgent(id));

// ── 状态 ────────────────────────────────────────────────
let snap = null;
const smooth = new Map();   // id → {x, z, yaw, speed, seed}
let selectedId = null;
let office = null;          // 静态家具实例（首帧构建）

function selectAgent(id) {
  selectedId = selectedId === id ? null : id;
  ui.select(selectedId);
}

// ── WebSocket（先于渲染器建立，隐藏标签页里 GPU 初始化可能挂起）──
function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (e) => {
    snap = JSON.parse(e.data);
    ui.onSnapshot(snap);
  };
  ws.onclose = () => setTimeout(connect, 1500);
  ws.onerror = () => ws.close();
}
connect();

const renderer = await Renderer3D.create(canvas);
if (!renderer) {
  document.getElementById("noGpu").classList.remove("hidden");
  throw new Error("WebGPU not available");
}

// ── 相机（拖拽环绕 + 滚轮缩放）───────────────────────────
const camera = { yaw: -0.22, pitch: 0.72, dist: 18.5, target: [0, 0.4, 0.6] };
let dragging = false, lastMX = 0, lastMY = 0, dragMoved = 0;

canvas.addEventListener("mousedown", (e) => {
  dragging = true; dragMoved = 0; lastMX = e.clientX; lastMY = e.clientY;
});
addEventListener("mousemove", (e) => {
  if (!dragging) return;
  const dx = e.clientX - lastMX, dy = e.clientY - lastMY;
  dragMoved += Math.abs(dx) + Math.abs(dy);
  lastMX = e.clientX; lastMY = e.clientY;
  camera.yaw -= dx * 0.005;
  camera.pitch = Math.min(1.25, Math.max(0.28, camera.pitch + dy * 0.004));
});
addEventListener("mouseup", () => { dragging = false; });
canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  camera.dist = Math.min(36, Math.max(11, camera.dist + e.deltaY * 0.02));
}, { passive: false });

// ── 点击拾取（投影头部位置找最近）────────────────────────
canvas.addEventListener("click", (e) => {
  if (!snap || dragMoved > 6) return;
  let best = null, bestD = 1e9;
  for (const a of snap.agents) {
    const s = smooth.get(a.id);
    if (!s) continue;
    const pr = renderer.project([s.x, 1.2, s.z]);
    if (!pr.visible) continue;
    const d = Math.hypot(e.clientX - pr.x, e.clientY - pr.y);
    if (d < bestD) { bestD = d; best = a.id; }
  }
  if (best && bestD < 60) selectAgent(best);
  else selectAgent(null);
});

addEventListener("resize", () => renderer.resize());

// ── 渲染循环 ─────────────────────────────────────────────
const t0 = performance.now();
let lastT = t0;

// 页面隐藏时 rAF 不触发，降级用定时器驱动；可见性切换时接管调度，
// 并通过取消旧句柄保证只存在一条调度链
let rafId = 0, toId = 0;
function schedule() {
  cancelAnimationFrame(rafId);
  clearTimeout(toId);
  if (document.hidden) toId = setTimeout(() => loop(performance.now()), 33);
  else rafId = requestAnimationFrame(loop);
}
document.addEventListener("visibilitychange", schedule);

function loop(now) {
  schedule();
  if (window.__pauseRender) return;
  const time = (now - t0) / 1000;
  const dt = Math.min((now - lastT) / 1000, 0.1);
  lastT = now;
  if (!snap) return;

  renderer.resize();
  if (!office) office = buildOffice(snap);

  // 插值 + 朝向
  const mc = snap.meeting.center;
  for (let i = 0; i < snap.agents.length; i++) {
    const a = snap.agents[i];
    let s = smooth.get(a.id);
    const wx = X(a.pos[0]), wz = Z(a.pos[1]);
    if (!s) {
      s = { x: wx, z: wz, yaw: Math.PI, speed: 0, seed: i * 0.77 + 0.31 };
      smooth.set(a.id, s);
    }
    const k = 1 - Math.exp(-dt * 8);
    const nx = s.x + (wx - s.x) * k;
    const nz = s.z + (wz - s.z) * k;
    const vx = (nx - s.x) / Math.max(dt, 1e-4);
    const vz = (nz - s.z) / Math.max(dt, 1e-4);
    s.speed = Math.hypot(vx, vz);
    s.x = nx; s.z = nz;

    // 目标朝向：走路→运动方向；拆解→面向会议桌；工作→面向屏幕(-z)
    let targetYaw;
    if (s.speed > 0.25) targetYaw = Math.atan2(vx, vz);
    else if (a.state === "meeting") targetYaw = Math.atan2(X(mc[0]) - s.x, Z(mc[1]) - s.z);
    else if (a.state === "working") targetYaw = Math.PI;
    else targetYaw = Math.PI * 0.85 + Math.sin(s.seed * 9) * 0.5;
    let dy = targetYaw - s.yaw;
    while (dy > Math.PI) dy -= Math.PI * 2;
    while (dy < -Math.PI) dy += Math.PI * 2;
    s.yaw += dy * Math.min(1, dt * 7);
  }

  // ── 组装场景 ──
  const O = { box: [...office.opaque.box], rbox: [...office.opaque.rbox],
              sphere: [...office.opaque.sphere], cyl: [...office.opaque.cyl] };
  const T = { box: [...office.transparent.box], rbox: [...office.transparent.rbox],
              sphere: [...office.transparent.sphere], cyl: [...office.transparent.cyl] };

  const labels = [];
  for (const a of snap.agents) {
    const s = smooth.get(a.id);
    const hop = s.speed > 0.25 ? Math.abs(Math.sin(time * 9 + s.seed * 6)) * Math.min(1, s.speed / 3) : 0;
    buildCharacter(O, T, a, { x: s.x, z: s.z, yaw: s.yaw, hop, speed: s.speed, seed: s.seed },
                   time, a.id === selectedId);
    buildScreen(O, a, time, s.seed);   // 工位屏幕：工作时代码流闪烁
    const head = renderer.project([s.x, 2.15, s.z]);
    const foot = renderer.project([s.x, 0, s.z]);
    labels.push({
      id: a.id, name: a.name, emoji: a.emoji, color: a.color,
      bubble: a.bubble, state: a.state,
      sx: head.x, sy: foot.y + 8, bx: head.x, by: head.y,
      visible: head.visible,
    });
  }

  // 协作光束
  const linkLabels = [];
  for (const l of snap.links) {
    const f = smooth.get(l.from), t = smooth.get(l.to);
    if (!f || !t) continue;
    const src = snap.agents.find((a) => a.id === l.from);
    buildLink(T, [f.x, f.z], [t.x, t.z], l.progress, src ? src.color : [1, 0.7, 0.9], time);
    const ox = f.x + (t.x - f.x) * l.progress, oz = f.z + (t.z - f.z) * l.progress;
    const pr = renderer.project([ox, 2.1, oz]);
    linkLabels.push({ label: l.label, x: pr.x, y: pr.y, visible: pr.visible });
  }

  // 工位区域名（投影桌子位置）
  const zoneLabels = snap.agents.map((a) => {
    const pr = renderer.project([X(a.desk[0]), 0, Z(a.desk[1]) + 1.3]);
    return { label: `${a.emoji} ${a.zone}`, x: pr.x, y: pr.y, visible: pr.visible };
  });
  const mcPr = renderer.project([X(mc[0]), 0, Z(mc[1]) + 2.2]);
  zoneLabels.push({ label: "🪑 会议室", x: mcPr.x, y: mcPr.y, visible: mcPr.visible });
  const paPr = renderer.project([X(snap.pantry[0]), 0, Z(snap.pantry[1]) + 2.2]);
  zoneLabels.push({ label: "☕ 茶水间", x: paPr.x, y: paPr.y, visible: paPr.visible });

  // 交付计数电子屏（投影到北墙发光板上）
  const total = snap.agents.reduce((n, a) => n + a.doneCount, 0);
  const hudPr = renderer.project([X(0.24), 2.55, WALL_Z + 0.45]);
  const hud = { text: `⚡ 已交付 ${total} 项`, x: hudPr.x, y: hudPr.y,
                visible: hudPr.visible };

  renderer.frame({
    time, minutes: snap.minutes, daylight: 1,   // 24 小时常亮
    camera, opaque: O, transparent: T,
  });
  ui.frame3d(labels, linkLabels, zoneLabels, hud);

  // 调试：同帧捕获画布
  if (window.__wantSnap) {
    window.__wantSnap = false;
    const c = document.createElement("canvas");
    c.width = canvas.width; c.height = canvas.height;
    c.getContext("2d").drawImage(canvas, 0, 0);
    fetch("/snap", { method: "POST", body: c.toDataURL("image/jpeg", 0.8) })
      .then(() => { window.__snapDone = true; });
  }
}
schedule();

// ─── 超级个体 · 3D 办公室场景 & Q版小人 ────────────────────
// 归一化世界坐标 [0,1]² → 3D 房间平面；所有家具/角色都由
// box/rbox/sphere/cyl 四种网格实例拼装。

export const MAP_W = 19, MAP_D = 12;           // 可活动区域
export const X = (nx) => (nx - 0.5) * MAP_W;
export const Z = (ny) => (ny - 0.5) * MAP_D;
export const WALL_Z = -MAP_D / 2 - 1.0;        // 北墙
const WALL_X = -MAP_W / 2 - 1.2;               // 西墙

const WOOD = [0.93, 0.83, 0.70];
const WOOD_DARK = [0.62, 0.48, 0.38];
const WALL_COL = [0.97, 0.93, 0.90];
const GLASS = [0.65, 0.85, 0.95];
const METAL = [0.72, 0.74, 0.82];
const DARK = [0.25, 0.23, 0.32];

// 会议桌旁的椅子位置（与后端 meeting_spot 完全一致的公式）
export function meetingSpot(center, radius, index, n) {
  const ang = (index / n) * Math.PI * 2 - Math.PI / 2;
  return [center[0] + Math.cos(ang) * radius,
          center[1] + Math.sin(ang) * radius * 0.8];
}

function push(dst, kind, inst) { dst[kind].push(inst); }

/** 构建静态办公室（只需构建一次）。snap 提供工位/会议/茶水间坐标。 */
export function buildOffice(snap) {
  const O = { box: [], rbox: [], sphere: [], cyl: [] };   // 不透明
  const T = { box: [], rbox: [], sphere: [], cyl: [] };   // 透明

  // ── 地板 & 踢脚 ──
  push(O, "box", { p: [0, -0.15, 0.35], s: [MAP_W + 2.8, 0.3, MAP_D + 3.4], c: [0.88, 0.80, 0.72] });
  // 地板拼木条纹
  for (let i = 0; i < 9; i++) {
    push(O, "box", {
      p: [-MAP_W / 2 + i * 2.4 + 1.2, 0.006, 0.35],
      s: [0.06, 0.02, MAP_D + 3.2], c: [0.80, 0.70, 0.60],
    });
  }
  // 工位区大地毯
  push(O, "box", { p: [X(0.27), 0.02, Z(0.53)], s: [11.2, 0.05, 8.6], c: [0.86, 0.90, 0.97] });

  // ── 墙壁 & 窗户 ──
  push(O, "box", { p: [0, 1.7, WALL_Z], s: [MAP_W + 3.2, 3.4, 0.35], c: WALL_COL });
  push(O, "box", { p: [WALL_X, 1.7, 0.35], s: [0.35, 3.4, MAP_D + 3.4], c: WALL_COL });
  for (let i = 0; i < 3; i++) {                     // 北墙三扇窗
    const wx = -6.4 + i * 6.4;
    push(O, "box", { p: [wx, 1.85, WALL_Z + 0.05], s: [3.6, 1.7, 0.1], c: [1, 1, 1] });
    push(T, "box", { p: [wx, 1.85, WALL_Z + 0.24], s: [3.3, 1.45, 0.06], c: [0.72, 0.90, 1.0], a: 0.5, glow: 0.55 });
  }
  push(O, "box", { p: [WALL_X + 0.05, 1.85, Z(0.55)], s: [0.1, 1.6, 4.2], c: [1, 1, 1] });  // 西墙窗
  push(T, "box", { p: [WALL_X + 0.26, 1.85, Z(0.55)], s: [0.06, 1.4, 3.9], c: [0.72, 0.90, 1.0], a: 0.5, glow: 0.55 });

  // ── 每个成员的工位（桌 + 屏幕 + 椅子 + 小地垫）──
  for (const a of snap.agents) {
    const dx = X(a.desk[0]), dz = Z(a.desk[1]);
    const tint = a.color.map((c) => 0.55 + c * 0.45);
    // 地垫
    push(O, "cyl", { p: [dx, 0.035, dz - 0.3], s: [3.4, 0.05, 2.6], c: tint });
    // 桌面（在椅子北侧）
    const tz = dz - 1.05;
    push(O, "rbox", { p: [dx, 0.78, tz], s: [2.3, 0.12, 1.0], c: WOOD });
    for (const [lx, lz] of [[-1, -0.35], [1, -0.35], [-1, 0.35], [1, 0.35]]) {
      push(O, "box", { p: [dx + lx * 1.0, 0.38, tz + lz], s: [0.09, 0.78, 0.09], c: WOOD_DARK });
    }
    // 显示器 + 底座（发光屏幕面由 buildScreen 每帧动态生成）
    push(O, "box", { p: [dx, 1.32, tz - 0.18], s: [1.0, 0.6, 0.06], c: DARK });
    push(O, "box", { p: [dx, 0.95, tz - 0.18], s: [0.09, 0.25, 0.09], c: DARK });
    push(O, "box", { p: [dx, 0.85, tz - 0.18], s: [0.42, 0.04, 0.28], c: DARK });
    // 键盘
    push(O, "box", { p: [dx, 0.86, tz + 0.25], s: [0.7, 0.04, 0.24], c: [0.92, 0.92, 0.96] });
    // 椅子（座+背+柱+底盘）
    push(O, "rbox", { p: [dx, 0.46, dz + 0.15], s: [0.62, 0.10, 0.62], c: a.color });
    push(O, "rbox", { p: [dx, 0.82, dz + 0.44], s: [0.62, 0.62, 0.10], c: a.color });
    push(O, "cyl", { p: [dx, 0.25, dz + 0.15], s: [0.08, 0.42, 0.08], c: METAL });
    push(O, "cyl", { p: [dx, 0.05, dz + 0.15], s: [0.55, 0.06, 0.55], c: METAL });
  }

  // ── 玻璃会议室（右侧区域）──
  const mc = snap.meeting.center;
  const mx = X(mc[0]), mz = Z(mc[1]);
  const gx = X(0.575);                              // 玻璃隔断竖线
  const gz = Z(0.575);                              // 玻璃隔断横线
  const glassPane = (p, s) => {
    push(T, "box", { p, s, c: GLASS, a: 0.16 });
    push(O, "box", { p: [p[0], 0.06, p[2]], s: [s[0] + 0.04, 0.12, s[2] + 0.04], c: METAL });
    push(O, "box", { p: [p[0], 2.35, p[2]], s: [s[0] + 0.04, 0.1, s[2] + 0.04], c: METAL });
  };
  // 竖隔断（留出下方门洞）
  glassPane([gx, 1.2, Z(0.16)], [0.08, 2.3, Z(0.42) - Z(-0.1)]);
  // 横隔断（右段，靠东墙，中间留门）
  glassPane([X(0.88), 1.2, gz], [X(1.06) - X(0.72), 2.3, 0.08]);
  // 会议大桌
  push(O, "rbox", { p: [mx, 0.80, mz], s: [3.7, 0.14, 2.0], c: [0.98, 0.94, 0.88] });
  push(O, "box", { p: [mx - 1.2, 0.4, mz], s: [0.16, 0.8, 1.5], c: WOOD_DARK });
  push(O, "box", { p: [mx + 1.2, 0.4, mz], s: [0.16, 0.8, 1.5], c: WOOD_DARK });
  // 围桌椅子（与站会站位一致）
  const n = snap.agents.length;
  for (let i = 0; i < n; i++) {
    const [nx, ny] = meetingSpot(mc, snap.meeting.radius, i, n);
    const cx = X(nx), cz = Z(ny);
    const yaw = Math.atan2(mx - cx, mz - cz);       // 面向桌心
    push(O, "rbox", { p: [cx, 0.42, cz], s: [0.58, 0.09, 0.58], c: [0.85, 0.82, 0.95], yaw });
    push(O, "cyl", { p: [cx, 0.2, cz], s: [0.07, 0.4, 0.07], c: METAL });
  }
  // 白板（北墙，会议室内）
  push(O, "box", { p: [mx, 1.75, WALL_Z + 0.28], s: [2.8, 1.5, 0.08], c: [1, 1, 1] });
  push(O, "box", { p: [mx, 1.75, WALL_Z + 0.26], s: [3.0, 1.65, 0.06], c: METAL });
  push(O, "box", { p: [mx - 0.6, 1.9, WALL_Z + 0.34], s: [1.0, 0.06, 0.02], c: [0.4, 0.6, 0.95] });
  push(O, "box", { p: [mx - 0.3, 1.62, WALL_Z + 0.34], s: [1.4, 0.06, 0.02], c: [0.95, 0.55, 0.65] });

  // ── 茶水间（右下角）──
  const px = X(snap.pantry[0]), pz = Z(snap.pantry[1]);
  push(O, "cyl", { p: [px, 0.8, pz], s: [1.9, 0.1, 1.9], c: [0.99, 0.96, 0.90] });
  push(O, "cyl", { p: [px, 0.4, pz], s: [0.18, 0.8, 0.18], c: WOOD_DARK });
  push(O, "cyl", { p: [px, 0.03, pz], s: [0.9, 0.06, 0.9], c: WOOD_DARK });
  for (let i = 0; i < 5; i++) {                     // 圆凳
    const ang = (i / 5) * Math.PI * 2;
    push(O, "cyl", {
      p: [px + Math.cos(ang) * 1.85, 0.28, pz + Math.sin(ang) * 1.55],
      s: [0.5, 0.55, 0.5], c: [0.95, 0.80, 0.72],
    });
  }
  // 咖啡机台 + 冰箱（靠东侧）
  push(O, "box", { p: [X(0.97), 0.55, Z(0.68)], s: [1.0, 1.1, 1.6], c: [0.92, 0.90, 0.95] });
  push(O, "box", { p: [X(0.97), 1.28, Z(0.63)], s: [0.5, 0.36, 0.45], c: DARK });
  push(O, "box", { p: [X(0.97), 1.0, Z(0.93)], s: [0.9, 2.0, 0.8], c: [0.88, 0.93, 0.97] });
  push(O, "box", { p: [X(0.945), 1.35, Z(0.905)], s: [0.04, 0.5, 0.06], c: METAL });

  // ── 交付计数电子屏（北墙，数字由 DOM 投影叠加）──
  push(O, "box", { p: [X(0.24), 2.55, WALL_Z + 0.28], s: [3.0, 1.0, 0.08], c: [0.14, 0.13, 0.24] });
  push(O, "box", { p: [X(0.24), 2.55, WALL_Z + 0.26], s: [3.2, 1.15, 0.06], c: METAL });
  push(T, "box", { p: [X(0.24), 2.55, WALL_Z + 0.33], s: [2.85, 0.85, 0.02], c: [0.30, 0.9, 0.75], a: 0.22, glow: 1 });

  // ── 绿植点缀 ──
  for (const [nx, ny] of [[0.035, 0.06], [0.53, 0.94], [0.035, 0.94]]) {
    const vx = X(nx), vz = Z(ny);
    push(O, "cyl", { p: [vx, 0.28, vz], s: [0.55, 0.55, 0.55], c: [0.85, 0.62, 0.52] });
    push(O, "sphere", { p: [vx, 0.95, vz], s: [1.05, 1.2, 1.05], c: [0.45, 0.74, 0.48] });
    push(O, "sphere", { p: [vx + 0.25, 1.25, vz - 0.1], s: [0.6, 0.7, 0.6], c: [0.52, 0.82, 0.55] });
  }

  return { opaque: O, transparent: T };
}

// ── Q 版小人 ────────────────────────────────────────────
const rot = (yaw, dx, dz) => [dx * Math.cos(yaw) + dz * Math.sin(yaw),
                              -dx * Math.sin(yaw) + dz * Math.cos(yaw)];
// 注意：与渲染器 vs 的旋转公式保持一致：x' = x·cy - z·sy, z' = x·sy + z·cy
const local = (yaw, px, py, pz, dx, dy, dz) => {
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  return [px + dx * cy - dz * sy, py + dy, pz + dx * sy + dz * cy];
};

/** 组装一个 Q 版分身。a=快照数据, m={x,z,yaw,hop,speed}, time=秒 */
export function buildCharacter(O, T, a, m, time, selected) {
  const col = a.color;
  const darker = col.map((c) => c * 0.6);
  const yaw = m.yaw;
  const px = m.x, pz = m.z;
  const sleeping = a.state === "sleeping";
  const working = a.state === "working";
  const breath = Math.sin(time * 2.6 + m.seed * 7) * 0.02;
  const hopY = m.hop * 0.38;
  const squash = 1 - m.hop * 0.08;

  // 影子
  T.sphere.push({ p: [px, 0.04, pz], s: [1.05, 0.05, 1.05], c: [0.2, 0.16, 0.3], a: 0.25 });

  // 身体（圆角方糖）
  O.rbox.push({ p: [px, 0.46 + hopY, pz], s: [0.80 / squash, 0.68 * squash, 0.62 / squash], c: col, yaw });
  // 肚皮
  O.sphere.push({ p: local(yaw, px, 0.42 + hopY, pz, 0, 0, 0.24), s: [0.52, 0.42, 0.2], c: col.map((c) => 0.65 + c * 0.35), yaw });

  // 头
  const hy = 1.22 + hopY + breath * (sleeping ? 0.4 : 1);
  O.sphere.push({ p: [px, hy, pz], s: [1.06, 0.98 + breath, 1.06], c: col, yaw });

  // 眼睛（睡觉/休息=闭眼横线，其他=圆眼+眨眼）
  const blinkT = Math.abs(Math.sin(time * 1.5 + m.seed * 13));
  const blink = sleeping || a.state === "resting" ? 0.06 : (blinkT > 0.97 ? 0.1 : 1);
  for (const sgn of [-1, 1]) {
    O.sphere.push({
      p: local(yaw, px, hy + 0.06, pz, sgn * 0.20, 0, 0.46),
      s: [0.13, 0.19 * blink, 0.07], c: [0.16, 0.13, 0.22], glow: 0.65, yaw,
    });
    // 腮红
    T.sphere.push({
      p: local(yaw, px, hy - 0.12, pz, sgn * 0.36, 0, 0.38),
      s: [0.17, 0.10, 0.06], c: [1.0, 0.55, 0.62], a: 0.5, yaw,
    });
  }
  // 嘴巴
  O.sphere.push({
    p: local(yaw, px, hy - 0.16, pz, 0, 0, 0.48),
    s: working ? [0.14, 0.035, 0.04] : [0.09, 0.07, 0.04],
    c: [0.42, 0.24, 0.3], glow: 0.4, yaw,
  });

  // 手手（工作=向前打字摆动，走路=前后摆）
  const swing = Math.sin(time * 8 + m.seed * 5);
  for (const sgn of [-1, 1]) {
    let dy = 0.55, dxo = sgn * 0.52, dzo = 0.08;
    if (working) { dy = 0.62 + swing * sgn * 0.03; dzo = 0.34; dxo = sgn * 0.4; }
    else if (m.speed > 0.06) { dzo = 0.1 + swing * sgn * 0.22; }
    O.sphere.push({
      p: local(yaw, px, dy + hopY, pz, dxo, 0, dzo),
      s: [0.24, 0.24, 0.24], c: col.map((c) => c * 0.92), yaw,
    });
  }
  // 脚脚
  for (const sgn of [-1, 1]) {
    const step = m.speed > 0.06 ? Math.sin(time * 10 + (sgn > 0 ? 0 : Math.PI)) * 0.16 : 0;
    O.sphere.push({
      p: local(yaw, px, 0.11 + Math.max(step, 0) * 0.5, pz, sgn * 0.22, 0, 0.08 + step),
      s: [0.26, 0.16, 0.3], c: darker, yaw,
    });
  }

  // 配饰
  const topY = hy + 0.52;
  switch (a.accessory) {
    case 1: { // 👑 皇冠
      O.cyl.push({ p: [px, topY, pz], s: [0.5, 0.22, 0.5], c: [1.0, 0.8, 0.25], yaw });
      O.sphere.push({ p: [px, topY + 0.17, pz], s: [0.12, 0.12, 0.12], c: [1.0, 0.35, 0.45], glow: 0.3 });
      break;
    }
    case 2: { // 👓 眼镜
      for (const sgn of [-1, 1]) {
        O.box.push({
          p: local(yaw, px, hy + 0.06, pz, sgn * 0.20, 0, 0.5),
          s: [0.26, 0.24, 0.04], c: [0.3, 0.3, 0.42], a: 1, yaw,
        });
      }
      O.box.push({ p: local(yaw, px, hy + 0.08, pz, 0, 0, 0.5), s: [0.14, 0.04, 0.03], c: [0.3, 0.3, 0.42], yaw });
      break;
    }
    case 3: { // 🎓 学士帽
      O.cyl.push({ p: [px, topY - 0.08, pz], s: [0.5, 0.18, 0.5], c: [0.22, 0.22, 0.38], yaw });
      O.box.push({ p: [px, topY + 0.05, pz], s: [0.95, 0.06, 0.95], c: [0.22, 0.22, 0.38], yaw: yaw + 0.5 });
      O.sphere.push({ p: local(yaw + 0.5, px, topY + 0.14, pz, 0.42, 0, 0.42), s: [0.1, 0.1, 0.1], c: [1.0, 0.8, 0.3], glow: 0.3 });
      break;
    }
    case 7: { // 🎀 蝴蝶结
      const b = local(yaw, px, topY - 0.05, pz, 0.3, 0, 0.12);
      for (const sgn of [-1, 1]) {
        O.sphere.push({ p: local(yaw, px, topY - 0.05, pz, 0.3 + sgn * 0.14, 0, 0.12), s: [0.2, 0.14, 0.1], c: [1.0, 0.42, 0.62], yaw });
      }
      O.sphere.push({ p: b, s: [0.09, 0.09, 0.09], c: [1.0, 0.6, 0.75] });
      break;
    }
    case 8: { // 🎨 贝雷帽
      O.sphere.push({ p: local(yaw, px, topY - 0.04, pz, -0.14, 0, 0), s: [0.75, 0.26, 0.75], c: [0.72, 0.35, 0.55], yaw });
      O.sphere.push({ p: local(yaw, px, topY + 0.12, pz, -0.2, 0, 0), s: [0.1, 0.1, 0.1], c: [0.72, 0.35, 0.55] });
      break;
    }
  }

  // 选中光环
  if (selected) {
    T.cyl.push({
      p: [px, 0.1, pz], s: [1.7 + Math.sin(time * 4) * 0.08, 0.04, 1.7 + Math.sin(time * 4) * 0.08],
      c: [1.0, 0.55, 0.8], a: 0.55, glow: 1,
    });
  }
}

/** 工位显示器发光屏（每帧动态：执行任务时闪烁代码流色，空闲时暗色待机）。 */
export function buildScreen(O, a, time, seed) {
  const dx = X(a.desk[0]), tz = Z(a.desk[1]) - 1.05;
  let c, glow;
  if (a.state === "working") {
    // 代码滚动感：青绿色明暗脉动 + 随机闪烁
    const pulse = 0.72 + 0.22 * Math.sin(time * 5 + seed * 9)
                + 0.08 * Math.sin(time * 23 + seed * 31);
    c = [0.18 * pulse, 0.95 * pulse, 0.62 * pulse];
    glow = 1;
  } else {
    c = [0.45, 0.58, 0.78];       // 待机淡蓝
    glow = 0.55;
  }
  O.box.push({ p: [dx, 1.32, tz - 0.14], s: [0.88, 0.48, 0.02], c, glow });
  if (a.state === "working") {    // 屏上几条"代码行"滚动
    for (let i = 0; i < 3; i++) {
      const yy = 1.44 - ((time * 0.35 + i * 0.16 + seed) % 0.42);
      O.box.push({
        p: [dx - 0.12 + (i % 2) * 0.1, yy, tz - 0.13],
        s: [0.42 - i * 0.09, 0.028, 0.012],
        c: [0.85, 1.0, 0.92], glow: 1,
      });
    }
  }
}

/** 协作光束 + 任务球（3D）。from/to 为世界坐标 [x,z] */
export function buildLink(T, from, to, progress, color, time) {
  const y = 1.7;
  const dx = to[0] - from[0], dz = to[1] - from[1];
  const len = Math.hypot(dx, dz);
  const yaw = Math.atan2(dx, dz);
  const segs = Math.max(6, Math.floor(len / 0.9));
  for (let i = 0; i < segs; i++) {
    const t = (i + 0.5) / segs;
    if ((i + time * 5) % 2 < 0.9) continue;      // 虚线流动
    T.box.push({
      p: [from[0] + dx * t, y, from[1] + dz * t],
      s: [0.09, 0.09, len / segs * 0.55], c: color, a: 0.45, glow: 1, yaw,
    });
  }
  // 任务球 + 拖尾
  for (let k = 0; k < 4; k++) {
    const t = Math.max(progress - k * 0.05, 0);
    const r = 0.24 - k * 0.045;
    T.sphere.push({
      p: [from[0] + dx * t, y + Math.sin(time * 6 + k) * 0.06, from[1] + dz * t],
      s: [r * 2, r * 2, r * 2],
      c: color.map((c) => 0.5 + c * 0.5), a: 0.95 - k * 0.2, glow: 1,
    });
  }
}

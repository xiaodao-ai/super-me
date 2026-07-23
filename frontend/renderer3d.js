// ─── 超级个体 · WebGPU 3D 渲染器 ──────────────────────────
// 低多边形可爱风：圆角盒 / 球 / 圆柱 三种网格实例化渲染，
// 半球环境光 + 定向光 + 昼夜天空背景。

// ── mat4 工具（列主序，与 WGSL 一致）─────────────────────
function mat4Perspective(fovY, aspect, near, far) {
  const f = 1 / Math.tan(fovY / 2);
  const nf = 1 / (near - far);
  return new Float32Array([
    f / aspect, 0, 0, 0,
    0, f, 0, 0,
    0, 0, (far + near) * nf, -1,
    0, 0, 2 * far * near * nf, 0,
  ]);
}
function mat4LookAt(eye, target, up) {
  const z = norm3(sub3(eye, target));
  const x = norm3(cross3(up, z));
  const y = cross3(z, x);
  return new Float32Array([
    x[0], y[0], z[0], 0,
    x[1], y[1], z[1], 0,
    x[2], y[2], z[2], 0,
    -dot3(x, eye), -dot3(y, eye), -dot3(z, eye), 1,
  ]);
}
function mat4Mul(a, b) { // a*b
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] +
                     a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
  }
  return o;
}
function transformPoint(m, p) {
  const x = p[0], y = p[1], z = p[2];
  const w = m[3] * x + m[7] * y + m[11] * z + m[15];
  return [
    (m[0] * x + m[4] * y + m[8] * z + m[12]) / w,
    (m[1] * x + m[5] * y + m[9] * z + m[13]) / w,
    (m[2] * x + m[6] * y + m[10] * z + m[14]) / w,
    w,
  ];
}
const sub3 = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross3 = (a, b) => [
  a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0],
];
function norm3(v) {
  const l = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / l, v[1] / l, v[2] / l];
}

// ── 网格生成（单位尺寸，实例缩放决定实际大小）──────────────
function buildRoundedBox(radius, seg) {
  // 通过"贴面网格 + SDF 圆角投影"生成圆角盒，边长 1
  const h = 0.5 - radius;
  const pos = [], nor = [], idx = [];
  const faces = [
    { n: [1, 0, 0], u: [0, 1, 0], v: [0, 0, 1] },
    { n: [-1, 0, 0], u: [0, 0, 1], v: [0, 1, 0] },
    { n: [0, 1, 0], u: [0, 0, 1], v: [1, 0, 0] },
    { n: [0, -1, 0], u: [1, 0, 0], v: [0, 0, 1] },
    { n: [0, 0, 1], u: [1, 0, 0], v: [0, 1, 0] },
    { n: [0, 0, -1], u: [0, 1, 0], v: [1, 0, 0] },
  ];
  for (const f of faces) {
    const base = pos.length / 3;
    for (let i = 0; i <= seg; i++) {
      for (let j = 0; j <= seg; j++) {
        const a = (i / seg - 0.5), b = (j / seg - 0.5);
        const p = [
          f.n[0] * 0.5 + f.u[0] * a + f.v[0] * b,
          f.n[1] * 0.5 + f.u[1] * a + f.v[1] * b,
          f.n[2] * 0.5 + f.u[2] * a + f.v[2] * b,
        ];
        const q = [
          Math.max(-h, Math.min(h, p[0])),
          Math.max(-h, Math.min(h, p[1])),
          Math.max(-h, Math.min(h, p[2])),
        ];
        const d = sub3(p, q);
        const n = Math.hypot(d[0], d[1], d[2]) > 1e-6 ? norm3(d) : f.n;
        pos.push(q[0] + n[0] * radius, q[1] + n[1] * radius, q[2] + n[2] * radius);
        nor.push(n[0], n[1], n[2]);
      }
    }
    for (let i = 0; i < seg; i++) {
      for (let j = 0; j < seg; j++) {
        const r0 = base + i * (seg + 1) + j, r1 = r0 + seg + 1;
        idx.push(r0, r0 + 1, r1, r1, r0 + 1, r1 + 1);
      }
    }
  }
  return { pos, nor, idx };
}

function buildSphere(lat, lon) {
  const pos = [], nor = [], idx = [];
  for (let i = 0; i <= lat; i++) {
    const th = (i / lat) * Math.PI;
    for (let j = 0; j <= lon; j++) {
      const ph = (j / lon) * Math.PI * 2;
      const n = [Math.sin(th) * Math.cos(ph), Math.cos(th), Math.sin(th) * Math.sin(ph)];
      pos.push(n[0] * 0.5, n[1] * 0.5, n[2] * 0.5);
      nor.push(n[0], n[1], n[2]);
    }
  }
  for (let i = 0; i < lat; i++) {
    for (let j = 0; j < lon; j++) {
      const r0 = i * (lon + 1) + j, r1 = r0 + lon + 1;
      idx.push(r0, r1, r0 + 1, r0 + 1, r1, r1 + 1);
    }
  }
  return { pos, nor, idx };
}

function buildCylinder(radial) {
  const pos = [], nor = [], idx = [];
  // 侧面
  for (let j = 0; j <= radial; j++) {
    const ph = (j / radial) * Math.PI * 2;
    const c = Math.cos(ph), s = Math.sin(ph);
    pos.push(c * 0.5, 0.5, s * 0.5); nor.push(c, 0, s);
    pos.push(c * 0.5, -0.5, s * 0.5); nor.push(c, 0, s);
  }
  for (let j = 0; j < radial; j++) {
    const r0 = j * 2;
    idx.push(r0, r0 + 2, r0 + 1, r0 + 1, r0 + 2, r0 + 3);
  }
  // 顶/底盖
  for (const top of [1, -1]) {
    const base = pos.length / 3;
    pos.push(0, 0.5 * top, 0); nor.push(0, top, 0);
    for (let j = 0; j <= radial; j++) {
      const ph = (j / radial) * Math.PI * 2 * top;
      pos.push(Math.cos(ph) * 0.5, 0.5 * top, Math.sin(ph) * 0.5);
      nor.push(0, top, 0);
    }
    for (let j = 0; j < radial; j++) {
      idx.push(base, base + 1 + j, base + 2 + j);
    }
  }
  return { pos, nor, idx };
}

// ── WGSL ────────────────────────────────────────────────
const MESH_WGSL = /* wgsl */ `
struct Globals {
  viewProj: mat4x4f,
  camPos: vec4f,
  lightDir: vec4f,
  misc: vec4f,   // daylight, time, minutes, _
};
struct Inst { a: vec4f, b: vec4f, c: vec4f }; // pos+yaw / scale+glow / color

@group(0) @binding(0) var<uniform> G: Globals;
@group(0) @binding(1) var<storage, read> inst: array<Inst>;

struct VOut {
  @builtin(position) pos: vec4f,
  @location(0) nrm: vec3f,
  @location(1) col: vec4f,
  @location(2) glow: f32,
  @location(3) wp: vec3f,
};

@vertex fn vs(@location(0) p: vec3f, @location(1) n: vec3f,
              @builtin(instance_index) ii: u32) -> VOut {
  let it = inst[ii];
  let cy = cos(it.a.w); let sy = sin(it.a.w);
  let sp = p * it.b.xyz;
  let rp = vec3f(sp.x * cy - sp.z * sy, sp.y, sp.x * sy + sp.z * cy);
  let wp = rp + it.a.xyz;
  let sn = normalize(n / max(abs(it.b.xyz), vec3f(1e-4)) * sign(it.b.xyz + vec3f(1e-9)));
  let rn = vec3f(sn.x * cy - sn.z * sy, sn.y, sn.x * sy + sn.z * cy);
  var o: VOut;
  o.pos = G.viewProj * vec4f(wp, 1.0);
  o.nrm = rn;
  o.col = it.c;
  o.glow = it.b.w;
  o.wp = wp;
  return o;
}

@fragment fn fs(v: VOut) -> @location(0) vec4f {
  let n = normalize(v.nrm);
  let day = G.misc.x;
  let l = normalize(G.lightDir.xyz);
  let diff = max(dot(n, l), 0.0);
  let skyCol = mix(vec3f(0.26, 0.27, 0.46), vec3f(0.78, 0.87, 1.00), day);
  let gndCol = mix(vec3f(0.19, 0.16, 0.30), vec3f(0.96, 0.88, 0.82), day);
  let hemi = mix(gndCol, skyCol, n.y * 0.5 + 0.5);
  let sunCol = mix(vec3f(0.55, 0.58, 0.95), vec3f(1.00, 0.97, 0.90), day);
  var lit = v.col.rgb * (hemi * 0.58 + sunCol * diff * mix(0.30, 0.72, day));
  let vd = normalize(G.camPos.xyz - v.wp);
  let hv = normalize(l + vd);
  lit += sunCol * pow(max(dot(n, hv), 0.0), 42.0) * 0.16 * max(day, 0.25);
  lit = mix(lit, v.col.rgb, v.glow);     // glow=1 → 自发光（眼睛/光球）
  return vec4f(lit, v.col.a);
}
`;

const SKY_WGSL = /* wgsl */ `
struct Globals {
  viewProj: mat4x4f,
  camPos: vec4f,
  lightDir: vec4f,
  misc: vec4f,   // daylight, time, minutes, _
};
@group(0) @binding(0) var<uniform> G: Globals;

struct VOut { @builtin(position) pos: vec4f, @location(0) uv: vec2f };

@vertex fn vs(@builtin(vertex_index) vi: u32) -> VOut {
  const P = array<vec2f, 3>(vec2f(-1.0, -1.0), vec2f(3.0, -1.0), vec2f(-1.0, 3.0));
  var o: VOut;
  o.pos = vec4f(P[vi], 0.999, 1.0);
  o.uv = P[vi] * vec2f(0.5, -0.5) + 0.5;
  return o;
}

fn hash2(p: vec2f) -> f32 {
  return fract(sin(dot(p, vec2f(127.1, 311.7))) * 43758.5453);
}

@fragment fn fs(v: VOut) -> @location(0) vec4f {
  // 24 小时常亮：始终使用明亮的白天天空
  let top = vec3f(0.55, 0.80, 1.00);
  let bot = vec3f(0.98, 0.92, 0.95);
  var col = mix(top, bot, v.uv.y);
  return vec4f(col, 1.0);
}
`;

const KINDS = ["box", "rbox", "sphere", "cyl"];
const MAX_INST = 1024;          // 每种网格 · 每个通道的实例上限

export class Renderer3D {
  static async create(canvas) {
    if (!navigator.gpu) return null;
    const adapter = await navigator.gpu.requestAdapter();
    if (!adapter) return null;
    const device = await adapter.requestDevice();
    const r = new Renderer3D();
    r.canvas = canvas;
    r.device = device;
    r.ctx = canvas.getContext("webgpu");
    r.format = navigator.gpu.getPreferredCanvasFormat();
    r.ctx.configure({ device, format: r.format, alphaMode: "opaque" });
    r.dpr = Math.min(window.devicePixelRatio || 1, 2);
    r.#initResources();
    r.resize();
    return r;
  }

  #initResources() {
    const d = this.device;
    this.uni = d.createBuffer({
      size: 112, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
    });

    // 网格
    const geos = {
      box: buildRoundedBox(0.06, 2),
      rbox: buildRoundedBox(0.24, 5),
      sphere: buildSphere(18, 26),
      cyl: buildCylinder(24),
    };
    this.meshes = {};
    for (const k of KINDS) {
      const g = geos[k];
      const vcount = g.pos.length / 3;
      const vdata = new Float32Array(vcount * 6);
      for (let i = 0; i < vcount; i++) {
        vdata.set([g.pos[i * 3], g.pos[i * 3 + 1], g.pos[i * 3 + 2],
                   g.nor[i * 3], g.nor[i * 3 + 1], g.nor[i * 3 + 2]], i * 6);
      }
      const vbuf = d.createBuffer({ size: vdata.byteLength, usage: GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST });
      d.queue.writeBuffer(vbuf, 0, vdata);
      const idata = new Uint16Array(Math.ceil(g.idx.length / 2) * 2);
      idata.set(g.idx);
      const ibuf = d.createBuffer({ size: idata.byteLength, usage: GPUBufferUsage.INDEX | GPUBufferUsage.COPY_DST });
      d.queue.writeBuffer(ibuf, 0, idata);
      this.meshes[k] = { vbuf, ibuf, count: g.idx.length };
    }

    // 管线
    const module = d.createShaderModule({ code: MESH_WGSL });
    const bgl = d.createBindGroupLayout({
      entries: [
        { binding: 0, visibility: GPUShaderStage.VERTEX | GPUShaderStage.FRAGMENT, buffer: { type: "uniform" } },
        { binding: 1, visibility: GPUShaderStage.VERTEX, buffer: { type: "read-only-storage" } },
      ],
    });
    const layout = d.createPipelineLayout({ bindGroupLayouts: [bgl] });
    const vertexState = {
      module, entryPoint: "vs",
      buffers: [{
        arrayStride: 24,
        attributes: [
          { shaderLocation: 0, offset: 0, format: "float32x3" },
          { shaderLocation: 1, offset: 12, format: "float32x3" },
        ],
      }],
    };
    const mkPipe = (blend, depthWrite) => d.createRenderPipeline({
      layout,
      vertex: vertexState,
      fragment: { module, entryPoint: "fs", targets: [{ format: this.format, blend }] },
      primitive: { topology: "triangle-list", cullMode: "back" },
      depthStencil: { format: "depth24plus", depthWriteEnabled: depthWrite, depthCompare: "less-equal" },
    });
    this.pipeOpaque = mkPipe(undefined, true);
    this.pipeTrans = mkPipe({
      color: { srcFactor: "src-alpha", dstFactor: "one-minus-src-alpha" },
      alpha: { srcFactor: "one", dstFactor: "one-minus-src-alpha" },
    }, false);

    // 天空
    const skyModule = d.createShaderModule({ code: SKY_WGSL });
    const skyBgl = d.createBindGroupLayout({
      entries: [{ binding: 0, visibility: GPUShaderStage.VERTEX | GPUShaderStage.FRAGMENT, buffer: { type: "uniform" } }],
    });
    this.pipeSky = d.createRenderPipeline({
      layout: d.createPipelineLayout({ bindGroupLayouts: [skyBgl] }),
      vertex: { module: skyModule, entryPoint: "vs" },
      fragment: { module: skyModule, entryPoint: "fs", targets: [{ format: this.format }] },
      primitive: { topology: "triangle-list" },
      depthStencil: { format: "depth24plus", depthWriteEnabled: false, depthCompare: "always" },
    });
    this.skyBind = d.createBindGroup({
      layout: skyBgl, entries: [{ binding: 0, resource: { buffer: this.uni } }],
    });

    // 每种网格 × (不透明/透明) 的实例缓冲
    this.channels = {};
    for (const k of KINDS) {
      for (const t of ["o", "t"]) {
        const buf = d.createBuffer({ size: MAX_INST * 48, usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST });
        const bind = d.createBindGroup({
          layout: bgl,
          entries: [
            { binding: 0, resource: { buffer: this.uni } },
            { binding: 1, resource: { buffer: buf } },
          ],
        });
        this.channels[k + t] = { buf, bind, data: new Float32Array(MAX_INST * 12) };
      }
    }
    this.depthTex = null;
  }

  resize() {
    const force = window.__forceSize;
    const cw = force ? force[0] : (this.canvas.clientWidth || window.innerWidth || 1280);
    const ch = force ? force[1] : (this.canvas.clientHeight || window.innerHeight || 800);
    const w = Math.floor(cw * this.dpr), h = Math.floor(ch * this.dpr);
    if (w > 0 && h > 0 && (this.canvas.width !== w || this.canvas.height !== h)) {
      this.canvas.width = w;
      this.canvas.height = h;
      this.depthTex?.destroy();
      this.depthTex = this.device.createTexture({
        size: [w, h], format: "depth24plus", usage: GPUTextureUsage.RENDER_ATTACHMENT,
      });
    }
    this.cssW = cw; this.cssH = ch;
  }

  /** 计算相机矩阵；camera: {yaw, pitch, dist, target:[x,y,z]} */
  updateCamera(camera, aspectOverride) {
    const { yaw, pitch, dist, target } = camera;
    const eye = [
      target[0] + dist * Math.cos(pitch) * Math.sin(yaw),
      target[1] + dist * Math.sin(pitch),
      target[2] + dist * Math.cos(pitch) * Math.cos(yaw),
    ];
    const aspect = aspectOverride || (this.canvas.width / Math.max(this.canvas.height, 1));
    const proj = mat4Perspective(38 * Math.PI / 180, aspect, 0.1, 200);
    const view = mat4LookAt(eye, target, [0, 1, 0]);
    this.viewProj = mat4Mul(proj, view);
    this.camEye = eye;
  }

  /** 世界坐标 → CSS 像素（供 DOM 标签定位）*/
  project(p) {
    if (!this.viewProj) return { x: -9999, y: -9999, visible: false };
    const c = transformPoint(this.viewProj, p);
    if (c[3] <= 0) return { x: -9999, y: -9999, visible: false };
    return {
      x: (c[0] * 0.5 + 0.5) * this.cssW,
      y: (-c[1] * 0.5 + 0.5) * this.cssH,
      visible: c[0] > -1.2 && c[0] < 1.2 && c[1] > -1.2 && c[1] < 1.2,
    };
  }

  /** scene: {time, minutes, daylight, camera, opaque:{kind:[inst]}, transparent:{kind:[inst]}}
   *  inst: {p:[x,y,z], s:[sx,sy,sz], yaw, c:[r,g,b], a, glow} */
  frame(scene) {
    const d = this.device;
    this.updateCamera(scene.camera);

    const uni = new Float32Array(28);
    uni.set(this.viewProj, 0);
    uni.set([...this.camEye, 0], 16);
    uni.set([0.45, 0.8, 0.35, 0], 20);   // 定向光（窗外阳光）
    uni.set([scene.daylight, scene.time, scene.minutes, 0], 24);
    d.queue.writeBuffer(this.uni, 0, uni);

    const counts = {};
    const pack = (chKey, list) => {
      const ch = this.channels[chKey];
      const n = Math.min(list.length, MAX_INST);
      for (let i = 0; i < n; i++) {
        const it = list[i], o = i * 12;
        ch.data[o] = it.p[0]; ch.data[o + 1] = it.p[1]; ch.data[o + 2] = it.p[2];
        ch.data[o + 3] = it.yaw || 0;
        ch.data[o + 4] = it.s[0]; ch.data[o + 5] = it.s[1]; ch.data[o + 6] = it.s[2];
        ch.data[o + 7] = it.glow || 0;
        ch.data[o + 8] = it.c[0]; ch.data[o + 9] = it.c[1]; ch.data[o + 10] = it.c[2];
        ch.data[o + 11] = it.a ?? 1;
      }
      if (n) d.queue.writeBuffer(ch.buf, 0, ch.data, 0, n * 12);
      counts[chKey] = n;
    };
    for (const k of KINDS) {
      pack(k + "o", scene.opaque[k] || []);
      // 透明按到相机距离从远到近排序
      const tl = (scene.transparent[k] || []).slice().sort((A, B) => {
        const da = (A.p[0] - this.camEye[0]) ** 2 + (A.p[1] - this.camEye[1]) ** 2 + (A.p[2] - this.camEye[2]) ** 2;
        const db = (B.p[0] - this.camEye[0]) ** 2 + (B.p[1] - this.camEye[1]) ** 2 + (B.p[2] - this.camEye[2]) ** 2;
        return db - da;
      });
      pack(k + "t", tl);
    }

    const enc = d.createCommandEncoder();
    const pass = enc.beginRenderPass({
      colorAttachments: [{
        view: this.ctx.getCurrentTexture().createView(),
        loadOp: "clear", storeOp: "store",
        clearValue: { r: 0.9, g: 0.9, b: 0.95, a: 1 },
      }],
      depthStencilAttachment: {
        view: this.depthTex.createView(),
        depthLoadOp: "clear", depthStoreOp: "store", depthClearValue: 1,
      },
    });
    pass.setPipeline(this.pipeSky);
    pass.setBindGroup(0, this.skyBind);
    pass.draw(3);

    const drawKind = (pipe, suffix) => {
      pass.setPipeline(pipe);
      for (const k of KINDS) {
        const n = counts[k + suffix];
        if (!n) continue;
        const m = this.meshes[k];
        pass.setBindGroup(0, this.channels[k + suffix].bind);
        pass.setVertexBuffer(0, m.vbuf);
        pass.setIndexBuffer(m.ibuf, "uint16");
        pass.drawIndexed(m.count, n);
      }
    };
    drawKind(this.pipeOpaque, "o");
    drawKind(this.pipeTrans, "t");
    pass.end();
    d.queue.submit([enc.finish()]);
  }
}

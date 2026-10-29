import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {STLLoader} from 'three/addons/loaders/STLLoader.js';
import {TransformControls} from 'three/addons/controls/TransformControls.js';
import {ConvexGeometry} from 'three/addons/geometries/ConvexGeometry.js';
import {LANGS, applyStatic, detectLang, getLang, setLang, t, zoneName} from './i18n.js';

const $ = id => document.getElementById(id);
const COLORS = ['#e4572e', '#29a3a3', '#f3a712', '#7768ae', '#4c9f38', '#d1495b', '#3b8ea5', '#c17c74', '#8fb339', '#b56dd6', '#5c80bc', '#e8a87c'];
const UNIT = {mm: 1, cm: 10, m: 1000, in: 25.4};
const D2R = Math.PI / 180;

const state = {
  helm: null,          // Antwort von /api/helm
  helmGeo: null,       // Rohgeometrie (Original-Koordinaten)
  head: null,          // Antwort von /api/kopf
  zones: [],
  sel: -1,
  preset: 'standard',
  result: null,
  dirty: false,
  tab: 'step-helm',    // aktiver Reiter in der Seitenleiste
  rev: 0,              // zählt jede Änderung – erkennt Änderungen während einer Berechnung
  running: false,      // Berechnung läuft (gegen Doppelklick)
  visited: new Set(['step-helm']),  // Fortschritt erst zeigen, wenn ein Reiter besucht wurde
};

/* ---------------- 3D-Szene ---------------- */
const wrap = $('canvas-wrap');
const renderer = new THREE.WebGLRenderer({antialias: true});
renderer.setPixelRatio(devicePixelRatio);
renderer.localClippingEnabled = true;
wrap.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color('#15181c');
const camera = new THREE.PerspectiveCamera(38, 1, 1, 6000);
camera.up.set(0, 0, 1);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
scene.add(new THREE.HemisphereLight(0xffffff, 0x3a3f46, 1.5));
const sun = new THREE.DirectionalLight(0xffffff, 1.6);
sun.position.set(300, 500, 600);
camera.add(sun); scene.add(camera);
const grid = new THREE.GridHelper(600, 24, 0x3a414b, 0x262b32);
grid.rotation.x = Math.PI / 2; grid.position.z = -170;
scene.add(grid);
const clipPlane = new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0);
const loader = new STLLoader();

const groups = {helm: new THREE.Group(), kopf: new THREE.Group(), ergebnis: new THREE.Group()};
Object.values(groups).forEach(g => scene.add(g));
let view = 'helm';

function resize() {
  const w = wrap.clientWidth, h = wrap.clientHeight;
  renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(wrap);
renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });

function material(color, opacity = 1, extra = {}) {
  return new THREE.MeshStandardMaterial({color, roughness: .6, metalness: .05, side: THREE.DoubleSide,
    transparent: opacity < 1, opacity, depthWrite: opacity >= 1, ...extra});
}
function clear(group) {
  for (const c of [...group.children]) {
    group.remove(c);
    c.traverse(o => { o.geometry?.dispose(); if (o.material) [].concat(o.material).forEach(m => { m.map?.dispose(); m.dispose(); }); });
  }
}
function frame(obj, dir = [1.1, 1.3, .6]) {
  const box = new THREE.Box3().setFromObject(obj);
  grid.visible = !box.isEmpty();  // ohne Modell kein verirrtes Bodenraster
  if (box.isEmpty()) return;
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const d = sphere.radius / Math.sin(camera.fov * D2R / 2) * 1.05;
  const v = new THREE.Vector3(...dir).normalize();
  controls.target.copy(sphere.center);
  camera.position.copy(sphere.center).addScaledVector(v, d);
  grid.position.z = box.min.z - 5;
}
function setView(v) {
  view = v;
  for (const [k, g] of Object.entries(groups)) g.visible = k === v;
  document.querySelectorAll('#views button').forEach(b => b.classList.toggle('active', b.dataset.view === v));
  $('layer-toggles').style.display = v === 'ergebnis' ? '' : 'none';
  $('empty').classList.toggle('hidden', v !== 'helm' || !!state.helmGeo);
  if (v !== 'helm' && state.picking) startPick(null);
  updateGizmo();
  frame(groups[v]);
}
document.querySelectorAll('#views button').forEach(b => b.onclick = () => setView(b.dataset.view));
$('reset-cam').onclick = () => frame(groups[view]);

/* ---------------- Hilfen ---------------- */
function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.classList.remove('hidden');
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add('hidden'), 6000);
}
function busy(on, text = t('busy.default')) { $('busy').classList.toggle('hidden', !on); $('busy-text').textContent = text; }
async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try {
      // Server-Fehler kommen als {code, args, text}: in der gewählten Sprache anzeigen
      const d = (await r.json()).detail;
      msg = d && d.code ? tr('err.' + d.code, d.args, d.text) : typeof d === 'string' ? d : JSON.stringify(d);
    } catch {}
    throw new Error(msg);
  }
  return r;
}
/** Übersetzung mit Rückfall auf einen festen Text, falls der Schlüssel fehlt. */
function tr(key, args, fallback) { const s = t(key, args || {}); return s === key ? (fallback ?? key) : s; }
const b64ToBuf = b => Uint8Array.from(atob(b), c => c.charCodeAt(0)).buffer;
const num = id => { const v = parseFloat($(id).value); return Number.isFinite(v) ? v : null; };
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

/* ---------------- 1 Helm ---------------- */
const MARKS = [['augen', '#3ecf6e'], ['scheitel', '#4d8dff'], ['kinn', '#f3a712'], ['ohr_links', '#b56dd6'], ['ohr_rechts', '#e4572e']];
const MARK_COLOR = Object.fromEntries(MARKS);
const markLabel = k => t('mark.' + k);
const markShort = k => tr(`mark.${k}.short`, {}, markLabel(k));
const ROT_PRESETS = {
  z: [[-1, 0, 0], [0, -1, 0], [0, 0, 1]],  // Z oben, Gesicht −Y (Blender)
  y: [[-1, 0, 0], [0, 0, 1], [0, 1, 0]],   // Y oben, Gesicht +Z
  reset: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
};
state.marks = {};     // Punkte in Datei-Koordinaten
state.picking = null; // welcher Punkt gerade gesetzt wird

// pivot = Drehung um die Modellmitte; inner = Einheit + Zentrierung, enthält Mesh und Marker in Datei-Koordinaten
const pivot = new THREE.Group(), inner = new THREE.Group();
pivot.add(inner);
let helmMesh = null;
const tc = new TransformControls(camera, renderer.domElement);
tc.setMode('rotate'); tc.setSpace('world'); tc.setSize(0.9); tc.setRotationSnap(5 * D2R);
tc.addEventListener('dragging-changed', e => { controls.enabled = !e.value; });
tc.addEventListener('objectChange', () => { showAngles(); markDirty(); });
scene.add(tc);

function rotationRows() {
  const e = new THREE.Matrix4().makeRotationFromQuaternion(pivot.quaternion).elements;
  return [0, 1, 2].map(i => [0, 1, 2].map(j => +e[j * 4 + i].toFixed(6)));
}
function setRotation(rows) {
  const m = new THREE.Matrix4().set(...rows[0], 0, ...rows[1], 0, ...rows[2], 0, 0, 0, 0, 1);
  pivot.quaternion.setFromRotationMatrix(m);
  showAngles(); markDirty();
}
function rotateBy(axis, deg) {
  const a = new THREE.Vector3(...{x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1]}[axis]);
  pivot.quaternion.premultiply(new THREE.Quaternion().setFromAxisAngle(a, deg * D2R));
  showAngles(); markDirty();
}
function showAngles() {
  const e = new THREE.Euler().setFromQuaternion(pivot.quaternion, 'ZYX'), f = v => Math.round(v / D2R) + 0;
  $('angles').textContent = state.helmGeo ? t('helm.angles', {x: f(e.x), y: f(e.y), z: f(e.z)}) : '';
}
function updateGizmo() {
  // Drehringe nur im Reiter "Helm", damit sie beim Markieren nicht im Weg sind
  const on = $('gizmo').checked && view === 'helm' && state.tab === 'step-helm' && !!state.helmGeo && !state.picking;
  if (on) tc.attach(pivot); else tc.detach();
  tc.enabled = on;
}
document.querySelectorAll('[data-preset]').forEach(b => b.onclick = () => {
  setRotation(ROT_PRESETS[b.dataset.preset]);
  state.orient = null; renderOrient();
});

/** Hinweis nach der automatischen Ausrichtung: Sicherheit, Tauschen, Rückgängig. */
function renderOrient() {
  const box = $('orient-box'), o = state.orient;
  $('orient-auto').disabled = !state.helm?.ausrichtung;
  box.classList.toggle('hidden', !o);
  if (!o) return;
  const why = o.gruende || [];
  let text = t('orient.' + o.sicherheit);
  if (why.includes('open_face')) text += ' ' + t('orient.tilt');
  else if (!why.includes('face_opening') && !why.includes('chin')) text += ' ' + t('orient.front');
  box.className = 'info orient ' + o.sicherheit;
  box.innerHTML = `<b>🧭 ${t('orient.h')}</b> ${text}
    <div class="row"><button class="mini" id="orient-ok">${t('orient.ok')}</button>
    <button class="mini" id="orient-flip">${t('orient.flip')}</button>
    <button class="mini" id="orient-undo">${t('orient.undo')}</button></div>`;
  $('orient-ok').onclick = () => { state.orient = null; renderOrient(); };
  $('orient-flip').onclick = () => rotateBy('z', 180);
  $('orient-undo').onclick = () => { setRotation(o.before || ROT_PRESETS.z); state.orient = null; renderOrient(); };
}
function applyAutoOrient() {
  const o = state.helm?.ausrichtung;
  if (!o) return;
  state.orient = {...o, before: rotationRows()};
  setRotation(o.rotation);
  renderOrient();
  if (helmMesh) frame(groups.helm);
}
$('orient-auto').onclick = applyAutoOrient;
document.querySelectorAll('[data-rot]').forEach(b => b.onclick = () => { const [a, d] = b.dataset.rot.split(','); rotateBy(a, +d); });
$('gizmo').onchange = updateGizmo;
$('snap').onchange = () => tc.setRotationSnap($('snap').checked ? 5 * D2R : null);

function textSprite(text, color, h = 14) {
  const c = document.createElement('canvas'), ctx = c.getContext('2d'), font = 'bold 44px system-ui, sans-serif';
  ctx.font = font; c.width = Math.ceil(ctx.measureText(text).width) + 28; c.height = 64;
  ctx.font = font; ctx.fillStyle = '#000b'; ctx.beginPath(); ctx.roundRect(0, 0, c.width, c.height, 14); ctx.fill();
  ctx.fillStyle = color; ctx.textBaseline = 'middle'; ctx.fillText(text, 14, 34);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({map: tex, depthTest: false}));
  s.scale.set(h * c.width / c.height, h, 1); s.renderOrder = 10;
  return s;
}

/* Punkte markieren */
function renderMarks() {
  const ul = $('marks'); ul.innerHTML = '';
  for (const [k, col] of MARKS) {
    const set = !!state.marks[k], active = state.picking === k;
    const li = document.createElement('li');
    li.className = 'mark' + (set ? ' set' : '') + (active ? ' active' : '');
    li.tabIndex = 0; li.setAttribute('role', 'button');
    li.onkeydown = e => { if ((e.key === 'Enter' || e.key === ' ') && e.target === li) { e.preventDefault(); li.click(); } };
    li.innerHTML = `<span class="dot" style="background:${col}"></span>${markLabel(k)}
      <span class="state">${active ? t('marks.picking') : set ? t('marks.set') : t('marks.unset')}</span>
      ${set ? `<button class="icon" title="${t('remove')}">✕</button>` : ''}`;
    li.onclick = e => {
      if (e.target.tagName === 'BUTTON') { delete state.marks[k]; marksChanged(); return; }
      startPick(active ? null : k);
    };
    ul.appendChild(li);
  }
  $('auto-align').disabled = !canAutoAlign();
}
function marksChanged() { renderMarks(); drawHelm(); markDirty(); }
function startPick(k) {
  if (k && !state.helmGeo) { toast(t('run.need.helm')); return; }
  state.picking = k;
  if (k && view !== 'helm') setView('helm');
  $('stage').classList.toggle('picking', !!k);
  $('pickbar').classList.toggle('hidden', !k);
  if (k) $('pick-text').textContent = t('pick.text', {name: markLabel(k)});
  renderMarks(); updateGizmo();
}
$('pick-cancel').onclick = () => startPick(null);
addEventListener('keydown', e => { if (e.key === 'Escape' && state.picking) startPick(null); });

const OPENING_MARKS = new Set(['augen', 'kinn', 'ohr_links', 'ohr_rechts']);
let hullMesh = null;
function helmHull() {
  // einmal pro Helm berechnet; unsichtbar, dient nur als Klickfläche über Öffnungen
  if (!hullMesh) {
    const pos = state.helmGeo.attributes.position, step = Math.max(1, Math.floor(pos.count / 40000));
    const pts = [];
    for (let i = 0; i < pos.count; i += step) pts.push(new THREE.Vector3().fromBufferAttribute(pos, i));
    hullMesh = new THREE.Mesh(new ConvexGeometry(pts), new THREE.MeshBasicMaterial({side: THREE.DoubleSide, visible: false}));
  }
  if (hullMesh.parent !== inner) inner.add(hullMesh);
  hullMesh.updateWorldMatrix(true, false);  // sonst trifft der erste Klick die Hülle an der falschen Stelle
  return hullMesh;
}

let downAt = null;
renderer.domElement.addEventListener('pointerdown', e => { downAt = [e.clientX, e.clientY]; });
renderer.domElement.addEventListener('pointerup', e => {
  if (view === 'kopf' && downAt && Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) <= 5) {
    const rect = renderer.domElement.getBoundingClientRect();
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2((e.clientX - rect.left) / rect.width * 2 - 1, -(e.clientY - rect.top) / rect.height * 2 + 1), camera);
    const hit = ray.intersectObjects(groups.kopf.children.filter(o => o.userData.zone !== undefined))[0];
    if (hit) {
      if (state.tab !== 'step-zonen') showTab('step-zonen');
      selectZone(hit.object.userData.zone, false);
    }
    return;
  }
  if (!state.picking || !helmMesh || !downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 5) return;
  const rect = renderer.domElement.getBoundingClientRect();
  const ndc = new THREE.Vector2((e.clientX - rect.left) / rect.width * 2 - 1, -(e.clientY - rect.top) / rect.height * 2 + 1);
  const ray = new THREE.Raycaster(); ray.setFromCamera(ndc, camera);
  let hit = ray.intersectObject(helmMesh)[0];
  // Augen, Kinn und Ohren liegen oft in Öffnungen: Trifft der Klick dort erst die Rückwand,
  // wird der Punkt in die Öffnungsfläche gelegt (konvexe Hülle über dem Loch).
  if (OPENING_MARKS.has(state.picking)) {
    const hullHit = ray.intersectObject(helmHull())[0];
    if (hullHit && (!hit || hit.distance - hullHit.distance > 4)) hit = hullHit;
  }
  if (!hit) { toast(t('toast.no_hit')); return; }
  const p = inner.worldToLocal(hit.point.clone());
  state.marks[state.picking] = [p.x, p.y, p.z];
  startPick(null); marksChanged();
});

function canAutoAlign() {
  const m = state.marks;
  return !!((m.ohr_links && m.ohr_rechts && (m.scheitel || m.kinn || m.augen)) || (m.scheitel && m.kinn && m.augen));
}
$('auto-align').onclick = () => {
  const m = Object.fromEntries(Object.entries(state.marks).map(([k, v]) => [k, new THREE.Vector3(...v)]));
  let X, Y, Z;
  if (m.ohr_links && m.ohr_rechts) {
    // Ohren geben die Querachse vor, Scheitel/Kinn/Augen die Höhe
    const mid = m.ohr_links.clone().add(m.ohr_rechts).multiplyScalar(0.5);
    X = m.ohr_rechts.clone().sub(m.ohr_links).normalize();
    Z = m.scheitel ? m.scheitel.clone().sub(mid) : m.kinn ? mid.clone().sub(m.kinn)
      : new THREE.Vector3().crossVectors(X, m.augen.clone().sub(mid));
    Z.addScaledVector(X, -Z.dot(X));
    if (Z.length() < 1e-6) { toast(t('toast.collinear')); return; }
    Z.normalize();
    Y = new THREE.Vector3().crossVectors(Z, X);
    if (m.augen && Y.dot(m.augen.clone().sub(mid)) < 0) {
      toast(t('toast.swapped'));
      X.negate(); Y.negate();
    }
  } else {
    // Scheitel–Kinn = senkrecht, Augen = vorne
    Z = m.scheitel.clone().sub(m.kinn).normalize();
    const mid = m.scheitel.clone().add(m.kinn).multiplyScalar(0.5);
    Y = m.augen.clone().sub(mid); Y.addScaledVector(Z, -Y.dot(Z));
    if (Y.length() < 1e-6) { toast(t('toast.eyes_line')); return; }
    Y.normalize();
    X = new THREE.Vector3().crossVectors(Y, Z);
  }
  setRotation([X.toArray(), Y.toArray(), Z.toArray()]);
  frame(groups.helm);
};

const drop = $('drop');
drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
drop.ondragleave = () => drop.classList.remove('over');
drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); if (e.dataTransfer.files[0]) uploadHelm(e.dataTransfer.files[0]); };
drop.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('file').click(); } });
$('file').onchange = e => { const f = e.target.files[0]; e.target.value = ''; if (f) uploadHelm(f); };

async function uploadHelm(file) {
  busy(true, t('helm.loading', {name: file.name}));
  try {
    const fd = new FormData(); fd.append('datei', file);
    const info = await (await api('/api/helm', {method: 'POST', body: fd})).json();
    const buf = await (await api(`/api/helm/${info.id}/vorschau.stl`)).arrayBuffer();
    state.helm = info;
    state.helmGeo = loader.parse(buf);
    hullMesh = null;
    state.marks = {};
    renderMarks();
    setRotation(ROT_PRESETS.z);
    state.orient = null;
    if (info.ausrichtung && !state.pendingProject) applyAutoOrient();
    renderOrient();
    $('einheit').value = info.einheit_vorschlag;
    renderHelmInfo();
    if (state.pendingProject) applyPendingProject();
    drawHelm(); setView('helm'); markDirty();
  } catch (e) { toast(t('toast.helm') + ': ' + e.message); }
  busy(false);
}

function updateHelmLock() {
  const loaded = !!state.helmGeo;
  document.querySelectorAll('#step-helm .row, #step-helm .rot-grid, #step-helm > .check, #marks, #auto-align')
    .forEach(el => el.classList.toggle('locked', !loaded));
  $('drop').classList.toggle('compact', loaded);
  $('drop').querySelector('strong').textContent = loaded ? t('helm.drop.again') : t('helm.drop.title');
}

function renderHelmInfo() {
  updateHelmLock();
  const info = state.helm;
  if (!info) return;
  const esc = s => s.replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  $('helm-info').innerHTML = `<b>${esc(info.name)}</b><br>`
    + t('helm.info', {n: info.dreiecke.toLocaleString(getLang()), dims: info.masse.map(v => v.toFixed(1)).join(' × ')})
    + (info.wasserdicht ? '' : '<br>' + t('helm.notclosed'));
  $('helm-info').classList.remove('hidden');
}

function drawHelm() {
  if (hullMesh) inner.remove(hullMesh);  // Hülle behalten, nicht mit aufräumen
  clear(inner); groups.helm.remove(pivot); clear(groups.helm); groups.helm.add(pivot);
  helmMesh = null;
  if (!state.helmGeo) { updateGizmo(); return; }
  const unit = UNIT[$('einheit').value];
  const geo = state.helmGeo.clone(); geo.computeVertexNormals(); geo.computeBoundingBox();
  const c = geo.boundingBox.getCenter(new THREE.Vector3());
  inner.scale.setScalar(unit); inner.position.copy(c).multiplyScalar(-unit);
  helmMesh = new THREE.Mesh(geo, material('#9aa4ad', 1));
  inner.add(helmMesh);
  for (const [k, p] of Object.entries(state.marks)) {
    const dot = new THREE.Mesh(new THREE.SphereGeometry(5, 20, 14), new THREE.MeshBasicMaterial({color: MARK_COLOR[k], depthTest: false}));
    dot.renderOrder = 9; dot.position.set(...p); dot.scale.setScalar(1 / unit);
    const lab = textSprite(markShort(k), MARK_COLOR[k]);
    lab.position.set(0, 0, 14); dot.add(lab);
    inner.add(dot);
  }
  // feste Zielrichtungen: dorthin muss der Helm gedreht werden
  const size = geo.boundingBox.getSize(new THREE.Vector3()).multiplyScalar(unit);
  const len = Math.max(size.x, size.y, size.z) * 0.75;
  for (const [dir, col, txt] of [[[0, 1, 0], '#3ecf6e', t('label.front')], [[0, 0, 1], '#4d8dff', t('label.up')]]) {
    const v = new THREE.Vector3(...dir);
    const arrow = new THREE.ArrowHelper(v, new THREE.Vector3(), len, col, 28, 16);
    arrow.traverse(o => { if (o.material) o.material.depthTest = false; o.renderOrder = 5; });
    const lab = textSprite(txt, col, 18); lab.position.copy(v).multiplyScalar(len + 24);
    groups.helm.add(arrow, lab);
  }
  showAngles(); updateGizmo();
}
$('einheit').onchange = () => { drawHelm(); setView('helm'); markDirty(); };

/* ---------------- 2 Kopf ---------------- */
const HEAD_FIELDS = ['umfang', 'laenge', 'breite', 'ohr_scheitel', 'gesicht'];
function measurements() {
  const m = {};
  for (const k of HEAD_FIELDS) { const v = num(k); if (v !== null) m[k] = v; }
  return m;
}
let headReq = 0;
const loadHead = debounce(async () => {
  const m = measurements();
  const example = !m.umfang;
  if (example) m.umfang = 570;  // Vorschau mit Beispielkopf, Berechnen bleibt gesperrt
  $('head-example').classList.toggle('hidden', !example);
  const req = ++headReq;
  try {
    const res = await (await api('/api/kopf', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(m)})).json();
    if (req !== headReq) return;  // inzwischen neuere Maße angefragt
    state.head = res;
    const i = res.info;
    $('laenge').placeholder = i.laenge_mm; $('breite').placeholder = i.breite_mm; $('ohr_scheitel').placeholder = i.ohr_bis_scheitel_mm;
    drawHead();
    if (['step-kopf', 'step-zonen'].includes(state.tab) && view !== 'kopf') setView('kopf');
    updateSteps();
  } catch (e) { if (req === headReq) toast(t('toast.kopf') + ': ' + e.message); }
}, 350);
HEAD_FIELDS.forEach(k => $(k).addEventListener('input', () => { loadHead(); markDirty(); }));
{
  const hint = document.createElement('p');
  hint.id = 'head-example'; hint.className = 'warn hidden'; hint.dataset.i18n = 'head.example';
  $('umfang').closest('.field').after(hint);
}

function headRadius(d) {
  const R = state.head.radial;
  const az = Math.atan2(d.x, d.y) / D2R, el = Math.asin(Math.max(-1, Math.min(1, d.z))) / D2R;
  const fi = (el + 90) / R.step, fj = (az + 180) / R.step;
  const i0 = Math.min(Math.floor(fi), R.n_el - 2), j0 = Math.min(Math.floor(fj), R.n_az - 2);
  const ti = fi - i0, tj = fj - j0, r = (i, j) => R.r[i * R.n_az + j];
  return (r(i0, j0) * (1 - tj) + r(i0, j0 + 1) * tj) * (1 - ti) + (r(i0 + 1, j0) * (1 - tj) + r(i0 + 1, j0 + 1) * tj) * ti;
}

/* ---------------- 3 Zonen ---------------- */
const dirAzEl = (az, el) => new THREE.Vector3(Math.cos(el * D2R) * Math.sin(az * D2R), Math.cos(el * D2R) * Math.cos(az * D2R), Math.sin(el * D2R));
function zoneFrame(z) {
  const dc = dirAzEl(z.az, z.el);
  let ea = new THREE.Vector3().crossVectors(dc, new THREE.Vector3(0, 0, 1));
  if (ea.length() < 1e-6) ea.set(Math.cos(z.az * D2R), -Math.sin(z.az * D2R), 0);  // wie zones.py
  ea.normalize();
  let eb = new THREE.Vector3().crossVectors(ea, dc);
  if (z.roll) {
    const r = z.roll * D2R, ea2 = ea.clone().multiplyScalar(Math.cos(r)).addScaledVector(eb, Math.sin(r));
    eb = eb.clone().multiplyScalar(Math.cos(r)).addScaledVector(ea, -Math.sin(r)); ea = ea2;
  }
  return {dc, ea, eb};
}
function zoneDir(z, f, X, Y) {
  const r = z.roundness;
  const Xm = (1 - r) * X + r * X * Math.sqrt(1 - Y * Y / 2), Ym = (1 - r) * Y + r * Y * Math.sqrt(1 - X * X / 2);
  const A = Xm * z.half_w * D2R, B = Ym * z.half_h * D2R;
  return f.dc.clone().multiplyScalar(Math.cos(B) * Math.cos(A)).addScaledVector(f.ea, Math.cos(B) * Math.sin(A)).addScaledVector(f.eb, Math.sin(B));
}
const toAzEl = d => ({az: Math.atan2(d.x, d.y) / D2R, el: Math.asin(Math.max(-1, Math.min(1, d.z))) / D2R});
function zoneSizeMm(z) {
  if (!state.head) return '';
  const r = headRadius(zoneFrame(z).dc);
  return `≈ ${Math.round(2 * z.half_w * D2R * r)} × ${Math.round(2 * z.half_h * D2R * r)} mm`;
}

function splitZone(i, axis) {
  const z = state.zones[i], f = zoneFrame(z), out = [];
  for (const s of [-1, 1]) {
    let dir, eaWant, child;
    if (axis === 'quer') {
      const A = s * z.half_w / 2 * D2R;
      dir = f.dc.clone().multiplyScalar(Math.cos(A)).addScaledVector(f.ea, Math.sin(A));
      eaWant = f.dc.clone().multiplyScalar(-Math.sin(A)).addScaledVector(f.ea, Math.cos(A));
      child = {...z, half_w: Math.max(4, z.half_w / 2 - 1)};
    } else {
      const B = s * z.half_h / 2 * D2R;
      dir = f.dc.clone().multiplyScalar(Math.cos(B)).addScaledVector(f.eb, Math.sin(B));
      eaWant = f.ea.clone();
      child = {...z, half_h: Math.max(4, z.half_h / 2 - 1)};
    }
    Object.assign(child, toAzEl(dir), {roll: 0, name: `${z.name}_${s < 0 ? 1 : 2}`});
    const f0 = zoneFrame(child);
    const roll = Math.atan2(eaWant.dot(f0.eb), eaWant.dot(f0.ea)) / D2R;
    child.roll = ((roll + 90) % 180 + 180) % 180 - 90;
    out.push(child);
  }
  state.zones.splice(i, 1, ...out);
  state.sel = i; state.preset = null;
  renderZones(); drawHead(); markDirty();
}

const SLIDERS = [  // [Feld, min, max, Schritt, Einheit]; Beschriftung über t('slider.' + Feld)
  ['az', -180, 180, 1, '°'],
  ['el', -15, 90, 1, '°'],
  ['half_w', 3, 60, 0.5, '°'],
  ['half_h', 3, 45, 0.5, '°'],
  ['roundness', 0, 1, 0.05, ''],
  ['roll', -90, 90, 1, '°'],
];

/** Kamera so drehen, dass die Zone zur Kamera zeigt (Abstand bleibt). */
function focusZone(i) {
  const z = state.zones[i];
  if (!z || view !== 'kopf') return;
  const dir = zoneFrame(z).dc.clone();
  const dist = camera.position.distanceTo(controls.target);
  dir.z = Math.max(dir.z, 0.25);  // nicht genau von oben/unten, damit die Ausrichtung erkennbar bleibt
  camera.position.copy(controls.target).addScaledVector(dir.normalize(), dist);
}
function selectZone(i, focus = true) {
  const keyboard = document.activeElement?.classList.contains('zone-head');
  state.sel = i;
  renderZones(); drawHead();
  if (keyboard) document.querySelectorAll('#zone-list .zone-head')[Math.max(i, 0)]?.focus();  // Fokus bleibt bei der Tastatur
  if (i >= 0 && focus) focusZone(i);
  document.querySelectorAll('#zone-list .zone')[i]?.scrollIntoView({block: 'nearest'});
}

function renderZones() {
  document.querySelectorAll('#presets button').forEach(b => b.classList.toggle('active', b.dataset.k === state.preset));
  const list = $('zone-list'); list.innerHTML = '';
  state.zones.forEach((z, i) => {
    const li = document.createElement('li');
    li.className = 'zone' + (i === state.sel ? ' sel' : '');
    li.innerHTML = `<div class="zone-head"><span class="sw" style="background:${COLORS[i % COLORS.length]}"></span>
      <input type="text" aria-label="${t('zone.name')}"><span class="size">${zoneSizeMm(z)}</span>
      <button class="icon" title="${t('remove')}" data-act="del">✕</button></div>
      <div class="zone-body"></div>`;
    const head = li.querySelector('.zone-head');
    head.tabIndex = 0; head.setAttribute('role', 'button'); head.setAttribute('aria-expanded', String(i === state.sel));
    head.onclick = e => { if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'BUTTON') selectZone(state.sel === i ? -1 : i); };
    head.onkeydown = e => { if ((e.key === 'Enter' || e.key === ' ') && e.target === head) { e.preventDefault(); head.click(); } };
    const name = li.querySelector('input');
    name.value = z.name;
    name.onfocus = () => {
      if (state.sel === i) return;
      state.sel = i; drawHead(); focusZone(i);
      list.querySelectorAll('.zone.sel').forEach(x => x.classList.remove('sel'));
      li.classList.add('sel');
    };
    name.onchange = () => { z.name = name.value.trim() || `${zoneName('polster')}_${i + 1}`; state.preset = null; markDirty(); };
    li.querySelector('[data-act=del]').onclick = () => {
      state.zones.splice(i, 1); state.sel = -1; state.preset = null; renderZones(); drawHead(); markDirty();
    };
    const body = li.querySelector('.zone-body');
    for (const [key, min, max, step, unit] of SLIDERS) {
      const row = document.createElement('label'); row.className = 'slider';
      row.innerHTML = `<span>${t('slider.' + key)}</span><input type="range" min="${min}" max="${max}" step="${step}" value="${z[key]}"><output></output>`;
      const inp = row.querySelector('input'), out = row.querySelector('output');
      const show = () => out.textContent = (step < 1 ? (+z[key]).toFixed(key === 'roundness' ? 2 : 1) : Math.round(z[key])) + unit;
      show();
      inp.oninput = () => {
        z[key] = parseFloat(inp.value); show(); state.preset = null;
        li.querySelector('.size').textContent = zoneSizeMm(z);
        drawHead(); markDirty();
      };
      body.appendChild(row);
    }
    const split = document.createElement('div'); split.className = 'split-row';
    split.innerHTML = `<button class="mini" data-a="quer">${t('zone.split.quer')}</button><button class="mini" data-a="laengs">${t('zone.split.laengs')}</button>`;
    split.querySelectorAll('button').forEach(b => b.onclick = () => splitZone(i, b.dataset.a));
    body.appendChild(split);
    list.appendChild(li);
  });
}

$('zone-add').onclick = () => {
  state.zones.push({name: `${zoneName('polster')}_${state.zones.length + 1}`, az: 0, el: 55, half_w: 14, half_h: 10, roundness: 0.6, roll: 0});
  state.sel = state.zones.length - 1; state.preset = null;
  renderZones(); drawHead(); setView('kopf'); markDirty();
};

async function loadPresets() {
  const p = await (await api('/api/vorlagen')).json();
  state.presets = p;
  renderPresets();
  applyPreset('standard');
}
function renderPresets() {
  const box = $('presets');
  box.innerHTML = '';
  for (const [k, v] of Object.entries(state.presets || {})) {
    const b = document.createElement('button'); b.dataset.k = k;
    b.innerHTML = `<b>${v.zonen.length}</b>${t('preset.parts')}<br><small>${t('preset.' + k)}</small>`;
    b.title = `${v.zonen.length} ${t('preset.parts')} – ${t('preset.' + k)}`;
    b.classList.toggle('active', k === state.preset);
    b.onclick = () => applyPreset(k);
    box.appendChild(b);
  }
}
function applyPreset(k) {
  // Polsternamen der Vorlage in der aktuellen Sprache (sie werden auch zu Dateinamen)
  state.zones = structuredClone(state.presets[k].zonen).map(z => ({...z, name: zoneName(z.name)}));
  state.preset = k; state.sel = -1;
  renderZones(); drawHead(); markDirty();
  if (state.head) setView('kopf');
}

function drawHead() {
  clear(groups.kopf);
  if (!state.head) return;
  const geo = loader.parse(b64ToBuf(state.head.stl)); geo.computeVertexNormals();
  groups.kopf.add(new THREE.Mesh(geo, material('#d9b38c', 1, {polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1})));
  const N = 22;
  state.zones.forEach((z, zi) => {
    const f = zoneFrame(z), color = new THREE.Color(COLORS[zi % COLORS.length]), sel = zi === state.sel;
    const pos = [];
    for (let i = 0; i <= N; i++) for (let j = 0; j <= N; j++) {
      const d = zoneDir(z, f, -1 + 2 * j / N, -1 + 2 * i / N);
      pos.push(...d.multiplyScalar(headRadius(d) + 1.2).toArray());
    }
    const idx = [];
    for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) {
      const a = i * (N + 1) + j, b = a + 1, c = a + N + 2, d = a + N + 1;
      idx.push(a, b, c, a, c, d);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); g.setIndex(idx); g.computeVertexNormals();
    const patch = new THREE.Mesh(g, material(color, sel ? 0.9 : 0.65));
    patch.userData.zone = zi;
    groups.kopf.add(patch);
    // Umriss
    const ring = [];
    const edge = (x, y) => { const d = zoneDir(z, f, x, y); ring.push(d.multiplyScalar(headRadius(d) + 1.6)); };
    for (let j = 0; j <= N; j++) edge(-1 + 2 * j / N, -1);
    for (let i = 1; i <= N; i++) edge(1, -1 + 2 * i / N);
    for (let j = N - 1; j >= 0; j--) edge(-1 + 2 * j / N, 1);
    for (let i = N - 1; i >= 0; i--) edge(-1, -1 + 2 * i / N);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(ring),
      new THREE.LineBasicMaterial({color: sel ? 0xffffff : color.clone().multiplyScalar(0.6)}));
    groups.kopf.add(line);
  });
  // Blickrichtung
  const ahead = headRadius(new THREE.Vector3(0, 1, 0)) + 12;
  const arrow = new THREE.ArrowHelper(new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, ahead, 0), 45, 0x3ecf6e, 14, 9);
  const lab = textSprite(t('label.front'), '#3ecf6e', 14);
  lab.material.depthTest = true;  // von hinten vom Kopf verdeckt – sonst stünde „vorne“ scheinbar am Hinterkopf
  lab.position.set(0, ahead + 62, 0);
  groups.kopf.add(arrow, lab);
}

/* ---------------- 4 Einstellungen ---------------- */
const scaleMode = () => document.querySelector('input[name=scale-mode]:checked').value;
document.querySelectorAll('input[name=scale-mode]').forEach(r => r.onchange = () => {
  $('scale-field').classList.toggle('hidden', scaleMode() !== 'fix');
  $('distort-field').classList.toggle('hidden', scaleMode() !== 'prop');
  markDirty();
});
for (const id of ['dicke', 'kompression', 'klett', 'kanaele', 'skalierung', 'verzerrung', 'vx', 'vy', 'vz']) $(id).addEventListener('input', markDirty);
for (const id of ['einbau', 'nummern']) $(id).addEventListener('change', markDirty);

/* Befestigung, Brille, Probestück, Zubehör */
const mountMode = () => document.querySelector('input[name=mount]:checked').value;
document.querySelectorAll('input[name=mount]').forEach(r => r.onchange = () => {
  $('magnet-opts').classList.toggle('hidden', mountMode() !== 'magnet');
  $('klett').closest('.field').classList.toggle('hidden', mountMode() === 'magnet');  // Magnethalter ersetzen das Klettband
  markDirty();
});
for (const id of ['magnet-d', 'magnet-h', 'acc-riemen-w', 'acc-mikro-d']) $(id).addEventListener('input', markDirty);
for (const id of ['brille', 'probestueck', 'acc-riemen', 'acc-luefter', 'acc-mikro', 'acc-luefter-s', 'muster-staerke', 'papier']) $(id).addEventListener('change', markDirty);
$('muster').addEventListener('change', () => { $('muster-opts').classList.toggle('hidden', !$('muster').checked); markDirty(); });

/* ---------------- 5 Teilen ---------------- */
const splitMode = () => document.querySelector('input[name=split-mode]:checked').value;
document.querySelectorAll('input[name=split-mode]').forEach(r => r.onchange = () => {
  $('split-opts').classList.toggle('hidden', splitMode() === 'keine');
  $('manual-cuts').classList.toggle('hidden', splitMode() !== 'manuell');
  drawCutPlanes(); markDirty();
});
const CUT_IDS = ['cut-x', 'cut-y', 'cut-z', 'cut-x-pos', 'cut-y-pos', 'cut-z-pos'];
for (const id of CUT_IDS) $(id).addEventListener('input', () => { drawCutPlanes(); markDirty(); });
for (const id of ['bed-x', 'bed-y', 'bed-z', 'dw-d', 'dw-l', 'dw-a', 'dw-s']) $(id).addEventListener('input', markDirty);
function manualCuts() {
  return ['x', 'y', 'z'].filter(a => $('cut-' + a).checked).map(a => ({achse: a, pos: num(`cut-${a}-pos`) ?? 0}));
}
function drawCutPlanes(result = state.result) {
  // Schnittebenen in der Ergebnis-Ansicht: bei "Selbst festlegen" live aus den Feldern, sonst aus dem Ergebnis
  for (const o of groups.ergebnis.children.filter(o => o.userData.layer === 'ebenen')) {
    groups.ergebnis.remove(o); o.geometry.dispose(); o.material.dispose();
  }
  if (!result || splitMode() === 'keine') return;
  const cuts = splitMode() === 'manuell' ? manualCuts() : result.bericht.teilung.schnitte;
  for (const c of cuts) {
    const plane = new THREE.Mesh(new THREE.PlaneGeometry(340, 340),
      new THREE.MeshBasicMaterial({color: '#4d8dff', transparent: true, opacity: 0.16, side: THREE.DoubleSide, depthWrite: false}));
    if (c.achse === 'x') plane.rotation.y = Math.PI / 2;
    if (c.achse === 'y') plane.rotation.x = Math.PI / 2;
    plane.position['xyz'['xyz'.indexOf(c.achse)]] = c.pos;
    if (c.achse !== 'z') plane.position.z = 20;
    plane.userData.layer = 'ebenen';
    groups.ergebnis.add(plane);
  }
  applyLayers();
}

/* ---------------- 5 Berechnen ---------------- */
function updateSummaries() {
  const radioText = name => document.querySelector(`input[name=${name}]:checked`)?.closest('label').querySelector('b').textContent || '';
  const opt = id => $(id).selectedOptions[0]?.textContent || '';
  $('sum-pads').textContent = `${num('dicke') ?? '–'} mm`;
  $('sum-size').textContent = radioText('scale-mode');
  $('sum-mount').textContent = `${radioText('mount')} · ${opt('einbau')}`;
  const extras = [['brille', 'glasses.short'], ['probestueck', 'dl.testpiece'], ['acc-riemen', 'dl.riemenhalter'],
    ['acc-luefter', 'dl.luefterhalter'], ['acc-mikro', 'dl.mikrofonclip']].filter(([id]) => $(id).checked).map(([, k]) => t(k));
  $('sum-extras').textContent = extras.length ? extras.join(', ') : t('sum.off');
  $('sum-foam').textContent = $('muster').checked ? `${opt('muster-staerke')} · ${$('papier').value}` : t('sum.off');
  const off = ['vx', 'vy', 'vz'].map(id => num(id) ?? 0);
  $('sum-offset').textContent = off.some(v => v) ? off.join(' / ') + ' mm' : t('sum.off');
}

function updateRunHint() {
  const ready = !!state.helm && !!num('umfang') && state.zones.length > 0;
  $('run').disabled = !ready || state.running;
  $('run').textContent = state.result ? t('run.again') : t('run.btn');
  updateSummaries();
  // Fehlt etwas, sagt der Hinweis was – mit Knopf direkt zur richtigen Stelle (keine Sackgasse)
  const miss = missingSteps()[0];
  const hint = $('run-hint');
  if (!miss) { hint.className = 'hint'; hint.textContent = t('run.ready'); return; }
  hint.className = 'warn';
  hint.innerHTML = `${t(miss.msg)} <button class="mini" type="button">${t('run.fix')}</button>`;
  hint.querySelector('button').onclick = () => { showTab(miss.tab); if (miss.focus) setTimeout(() => $(miss.focus).focus(), 50); };
}
/** Pflichtangaben, die noch fehlen (in Reihenfolge der Schritte). */
function missingSteps() {
  const list = [];
  if (!state.helm) list.push({tab: 'step-helm', msg: 'run.need.helm'});
  if (!num('umfang')) list.push({tab: 'step-kopf', msg: 'run.need.umfang', focus: 'umfang'});
  if (!state.zones.length) list.push({tab: 'step-zonen', msg: 'run.need.zone'});
  return list;
}
function showStale() {
  if ($('result').querySelector('.stale')) return;
  const s = document.createElement('div'); s.className = 'stale';
  s.textContent = t('run.stale');
  $('result').prepend(s);
}
function markDirty() {
  state.rev++;
  updateRunHint();
  if (state.result && !state.dirty) { state.dirty = true; showStale(); }
  updateSteps();
}

$('run').onclick = async () => {
  if (state.running) return;
  state.running = true; updateRunHint();
  const sentRev = state.rev;
  const body = {
    helm_id: state.helm.id, einheit: $('einheit').value, rotation: rotationRows(), markierungen: state.marks,
    masse: measurements(), zonen: state.zones,
    dicke: num('dicke') ?? 10, kompression: num('kompression') ?? 2, klett: num('klett') ?? 3, kanaele: num('kanaele') ?? 0,
    skalierung: scaleMode() === 'fix' ? (num('skalierung') ?? 100) / 100 : null,
    proportional: scaleMode() === 'prop', max_verzerrung: num('verzerrung') ?? 12,
    einbau_markierung: $('einbau').value, polster_nummern: $('nummern').checked,
    teilung: splitMode(), schnitte: manualCuts(), bauraum: [num('bed-x') ?? 256, num('bed-y') ?? 256, num('bed-z') ?? 256],
    duebel_d: num('dw-d') ?? 5, duebel_l: num('dw-l') ?? 16, duebel_abstand: num('dw-a') ?? 55, duebel_spiel: num('dw-s') ?? 0.15,
    versatz: [num('vx') ?? 0, num('vy') ?? 0, num('vz') ?? 0],
    probestueck: $('probestueck').checked, befestigung: mountMode(),
    magnet_d: num('magnet-d') ?? 10, magnet_h: num('magnet-h') ?? 3, brille: $('brille').checked,
    riemen: $('acc-riemen').checked ? (num('acc-riemen-w') ?? 25) : null,
    luefter: $('acc-luefter').checked ? +$('acc-luefter-s').value : null,
    mikrofon: $('acc-mikro').checked ? (num('acc-mikro-d') ?? 6) : null,
    muster: $('muster').checked, muster_staerke: +$('muster-staerke').value, papier: $('papier').value,
  };
  busy(true, t('run.busy'));
  try {
    const res = await (await api('/api/berechnen', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})).json();
    await drawResult(res);
    state.result = res;
    state.dirty = state.rev !== sentRev;  // während der Berechnung geändert -> Ergebnis gleich als veraltet zeigen
    renderResult(res);
    document.querySelector('#views [data-view=ergebnis]').disabled = false;
    setView('ergebnis');
  } catch (e) { toast(t('toast.calc') + ': ' + e.message); }
  finally {
    state.running = false;
    busy(false); updateRunHint(); updateSteps();
  }
};

async function loadStl(url) { const g = loader.parse(await (await api(url)).arrayBuffer()); g.computeVertexNormals(); return g; }

async function drawResult(res) {
  clear(groups.ergebnis);
  const f = res.dateien;
  const split = f.teile.length > 0;
  const [head, orig, pads, helmParts, dowels] = await Promise.all([
    loadStl(f.kopf), loadStl(f.original), Promise.all(f.polster.map(p => loadStl(p.vorschau))),
    Promise.all(split ? f.teile.map(t => loadStl(t.vorschau)) : [loadStl(f.helm)]),
    f.duebel ? loadStl(f.duebel.vorschau) : null]);
  const add = (obj, layer) => { obj.userData.layer = layer; groups.ergebnis.add(obj); return obj; };
  add(new THREE.Mesh(head, material('#d9b38c')), 'kopf');
  const PART_TINTS = ['#9aa4ad', '#b3a68f', '#8fa7b3', '#a99bb5', '#9fb39a', '#b59a9a', '#a8b39a', '#9a9fb5'];
  helmParts.forEach((g, i) => {
    const m = add(new THREE.Mesh(g, material(split ? PART_TINTS[i % PART_TINTS.length] : '#9aa4ad', 0.3)), 'helm');
    // Richtung zum Auseinanderziehen: weg von allen Schnitten, auf deren Seite das Teil liegt
    const v = new THREE.Vector3();
    for (const [axis, sign] of split ? f.teile[i].seiten : []) v['xyz'['xyz'.indexOf(axis)]] += sign * 28;
    m.userData.explode = v;
  });
  if (dowels) add(new THREE.Mesh(dowels, material('#ff8a3d', 1, {emissive: '#5a2a00'})), 'duebel');
  $('layer-toggles').classList.toggle('has-split', split);
  add(new THREE.Mesh(orig, material('#f3a712', 0.16, {emissive: '#5a3a00'})), 'original');
  pads.forEach((g, i) => add(new THREE.Mesh(g, material(COLORS[i % COLORS.length])), 'polster'));
  f.polster.forEach((p, i) => {
    const lab = textSprite(String(p.nummer), '#ffffff', 16);
    lab.material.color = new THREE.Color(COLORS[i % COLORS.length]).lerp(new THREE.Color('#ffffff'), 0.55);
    lab.position.set(...p.mitte);
    add(lab, 'nummern');
  });
  drawCutPlanes(res);
  applyLayers();
}
function applyLayers() {
  const cut = $('l-schnitt').checked;
  // ohne Kopf ist der Helm undurchsichtig, damit die eingravierten Einbau-Markierungen sichtbar sind
  const solidHelm = !$('l-kopf').checked;
  const explode = $('l-explode').checked;
  groups.ergebnis.children.forEach(m => {
    m.visible = $('l-' + m.userData.layer).checked;
    if (m.userData.explode) m.position.copy(explode ? m.userData.explode : new THREE.Vector3());
    if (m.isMesh) m.material.clippingPlanes = cut ? [clipPlane] : [];
    if (m.userData.layer === 'helm') {
      Object.assign(m.material, {opacity: solidHelm ? 1 : 0.3, transparent: !solidHelm, depthWrite: solidHelm});
      m.material.needsUpdate = true;
    }
  });
}
['l-kopf', 'l-helm', 'l-polster', 'l-original', 'l-nummern', 'l-schnitt', 'l-duebel', 'l-explode', 'l-ebenen']
  .forEach(id => $(id).onchange = applyLayers);

function installView() {
  // Blick von unten in den Helm, Kopf ausgeblendet: so sitzen die Polster beim Einkleben
  $('l-kopf').checked = false; $('l-helm').checked = true; $('l-polster').checked = true; $('l-nummern').checked = true;
  $('l-schnitt').checked = false; $('l-original').checked = false; $('l-explode').checked = false; $('l-ebenen').checked = false;
  applyLayers(); setView('ergebnis');
  controls.target.set(0, 0, 40);
  camera.position.set(0, -160, -420);
}

/** Anzeigename eines Helmteils aus Nummer und Seiten, z.B. "Teil 2 rechts hinten". */
function partName(nr, sides) {
  return `${t('part')} ${nr} ` + (sides || []).map(([axis, sign]) => t(`side.${axis}${sign > 0 ? '+' : '-'}`)).join(' ');
}
/** Server-Meldung {code, args, text} in der gewählten Sprache. */
function messageText(m) {
  const args = {...m.args};
  for (const [k, v] of Object.entries(args)) if (typeof v === 'number' && !Number.isInteger(v)) args[k] = fmt(v, 2);
  if (args.axis) args.axis = String(args.axis).toUpperCase();
  if (m.code === 'part_too_big') args.part = partName(args.nr, args.sides);
  return tr('warn.' + m.code, args, m.text);
}
const fmt = (v, digits) => Number(v).toLocaleString(getLang(), {maximumFractionDigits: digits});

function splitHtml(res) {
  const sp = res.bericht.teilung, f = res.dateien;
  if (sp.modus === 'keine') return '';
  if (!f.teile.length) return `<p class="hint top">${sp.hinweis ? messageText(sp.hinweis) : t('split.nothing')}</p>`;
  const rows = f.teile.map((p, i) => `<tr><td colspan="2">${partName(i + 1, p.seiten)}</td>
    <td>${p.masse_mm.map(Math.round).join('×')}</td><td class="${p.passt ? 'ok' : 'bad'}">${p.passt ? t('fits') : t('toobig')}</td>
    <td><a href="${p.druck}" download>STL</a></td></tr>`).join('');
  const d = sp.duebel;
  const dowels = d && d.anzahl ? `<p class="hint top">${t('dowel.res', {n: d.anzahl, d: fmt(d.durchmesser_mm, 1),
      l: fmt(d.laenge_mm, 1), h: fmt(d.loch_mm, 2)})} ·
      <a href="${f.duebel.satz}" download>${t('dowel.set', {n: d.anzahl})}</a> · <a href="${f.duebel.einzeln}" download>${t('dowel.single')}</a></p>` : '';
  return `<h3>${t('parts.h')}</h3>
    <table class="pads"><tr><th colspan="2">${t('th.part')}</th><th>${t('th.print')}</th><th>${t('th.bed')}</th><th></th></tr>${rows}</table>${dowels}
    <p class="hint top">${t('parts.hint')}</p>`;
}

/* Material, Kosten, Zeit – grobe Schätzung aus Volumen und Oberfläche (der Slicer weiß es genauer) */
const MAT_DEFAULTS = {tpuPrice: 30, plaPrice: 20, tpuFill: 12, plaFill: 15};
let matSettings = {...MAT_DEFAULTS};
try { Object.assign(matSettings, JSON.parse(localStorage.getItem('helmpad.material') || '{}')); } catch {}
const MATERIAL = {  // Dichte g/cm³, Wandstärke mm, Durchfluss mm³/s
  tpu: {density: 1.21, wall: 0.8, flow: 2.5},
  pla: {density: 1.24, wall: 1.2, flow: 10},
};
function estimate(item) {
  const solid = item.gruppe === 'pla_voll', base = item.gruppe === 'tpu' ? 'tpu' : 'pla', m = MATERIAL[base];
  const fill = solid ? 1 : (base === 'tpu' ? matSettings.tpuFill : matSettings.plaFill) / 100;
  const vol = item.volumen_cm3 * 1000, shell = Math.min(vol, item.flaeche_cm2 * 100 * m.wall);
  const used = (shell + fill * (vol - shell)) * item.anzahl;  // mm³
  const grams = used / 1000 * m.density;
  return {base, grams, hours: used / m.flow / 3600 * 1.25,
          cost: grams / 1000 * (base === 'tpu' ? matSettings.tpuPrice : matSettings.plaPrice)};
}
function materialHtml(res) {
  const sum = {tpu: {grams: 0, hours: 0, cost: 0}, pla: {grams: 0, hours: 0, cost: 0}};
  for (const it of res.bericht.material || []) {
    const e = estimate(it);
    for (const k of ['grams', 'hours', 'cost']) sum[e.base][k] += e[k];
  }
  const row = (label, v) => `<tr><td>${label}</td><td>${fmt(v.grams, 0)} g</td><td>${fmt(v.hours, 1)} h</td><td>${fmt(v.cost, 2)} €</td></tr>`;
  const total = {grams: sum.tpu.grams + sum.pla.grams, hours: sum.tpu.hours + sum.pla.hours, cost: sum.tpu.cost + sum.pla.cost};
  const inp = (id, key, unit, step) => `<label class="field"><span>${t('mat.' + key)}</span>
      <input type="number" id="${id}" data-key="${key}" value="${matSettings[key]}" min="0" step="${step}"><i>${unit}</i></label>`;
  return `<div class="mat-box"><b>${t('mat.h')}</b>
    <div class="grid2">${inp('mat-tp', 'tpuPrice', '€/kg', 1)}${inp('mat-pp', 'plaPrice', '€/kg', 1)}
      ${inp('mat-tf', 'tpuFill', '%', 1)}${inp('mat-pf', 'plaFill', '%', 1)}</div>
    <table><tr><th></th><th>${t('mat.weight')}</th><th>${t('mat.time')}</th><th>${t('mat.cost')}</th></tr>
      ${row('TPU', sum.tpu)}${row('PLA/PETG', sum.pla)}<tr class="total">${row(t('mat.total'), total).slice(4)}</table>
    <p class="hint top">${t('mat.hint')}</p></div>`;
}
function bindMaterialInputs() {
  document.querySelectorAll('.mat-box input').forEach(inp => inp.onchange = () => {
    const v = parseFloat(inp.value);
    if (Number.isFinite(v) && v >= 0) matSettings[inp.dataset.key] = v;
    try { localStorage.setItem('helmpad.material', JSON.stringify(matSettings)); } catch {}
    const box = document.querySelector('.mat-box');
    box.outerHTML = materialHtml(state.result);
    bindMaterialInputs();
  });
}
function foamHtml(res) {
  const m = res.bericht.schnittmuster, f = res.dateien.schnittmuster;
  if (!m || !f) return '';
  return `<h3>${t('foam.res.h')}</h3>
    <p class="hint top">${t('foam.res.info', {n: m.teile, p: m.seiten, s: fmt(m.staerke_mm, 1), paper: m.papier})}</p>
    <div class="foam-preview"><img src="${f.svg}" alt="${t('foam.res.h')}"></div>
    <div class="dl-list"><a href="${f.pdf}" download>${t('foam.pdf')}</a><a href="${f.svg}" download>${t('foam.svg')}</a>
      <a href="${f.dxf}" download>${t('foam.dxf')}</a></div>
    <ol class="steps-list">${[1, 2, 3, 4].map(i => `<li>${t('foam.step' + i)}</li>`).join('')}</ol>`;
}
function extrasHtml(res) {
  const f = res.dateien, r = res.bericht, links = [];
  if (f.probestueck) links.push(`<a href="${f.probestueck}" download>${t('dl.testpiece')}</a>`);
  for (const e of f.zubehoer || []) links.push(`<a href="${e.druck}" download>${t('dl.' + e.teil)}${e.anzahl > 1 ? ` (${e.anzahl}×)` : ''}</a>`);
  const plates = [...(f.platten?.tpu || []).map((p, i) => [p, `TPU ${i + 1}`]), ...(f.platten?.pla || []).map((p, i) => [p, `PLA ${i + 1}`])];
  const mount = r.befestigung?.art === 'magnet'
    ? `<p class="hint top">${t('mount.result', {n: r.befestigung.magnete, d: fmt(r.befestigung.magnet_mm[0], 1), h: fmt(r.befestigung.magnet_mm[1], 1)})}</p>` : '';
  return (links.length ? `<h3>${t('dl.extras')}</h3><div class="dl-list">${links.join('')}</div>` : '') + mount
    + (plates.length ? `<h3>${t('dl.plates')}</h3><div class="dl-list">${plates.map(([p, l]) =>
      `<a href="${p.url}" download title="${p.teile.join(', ')}">${t('dl.plate', {name: l, n: p.teile.length})}</a>`).join('')}</div>
      <p class="hint top">${t('dl.plates.hint')}</p>` : '');
}

/** Jeden Abschnitt mit eigener Überschrift (<h3>) in einen eigenen aufklappbaren Block verwandeln. */
function wrapRes(html) {
  if (!html) return '';
  const parts = html.split(/<h3>/);
  return parts[0] + parts.slice(1).map(part => {
    const [title, ...rest] = part.split('</h3>');
    return `<details class="res"><summary>${title}</summary>${rest.join('</h3>')}</details>`;
  }).join('');
}

function renderResult(res) {
  const r = res.bericht, el = $('result'), h = r.helm;
  const rows = res.dateien.polster.map((p, i) => {
    const s = r.polster[p.name];
    return `<tr><td><b>${p.nummer}</b></td><td><span class="sw" style="display:inline-block;width:9px;height:9px;border-radius:2px;background:${COLORS[i % COLORS.length]}"></span> ${p.name}</td>
      <td>${s.masse_mm.slice(0, 2).map(Math.round).join('×')}</td><td>${fmt(s.dicke_min_mm, 1)}–${fmt(s.dicke_max_mm, 1)}</td>
      <td><a href="${p.druck}" download>STL</a></td></tr>`;
  }).join('');
  const ax = h.skalierung_achsen_prozent, pct = v => fmt(v, 1) + ' %';
  const scaleText = h.proportional
    ? `<div class="stat"><span>${t('res.scaled.axes')}</span><b style="font-size:14px">${pct(ax.breite)} · ${pct(ax.laenge)} · ${pct(ax.hoehe)}</b></div>`
    : `<div class="stat"><span>${t('res.scaled')}</span><b>${pct(h.skalierung_prozent)}</b></div>`;
  const mm = a => a.map(Math.round).join(' × ');
  const warnings = r.warnungen_codes || r.warnungen.map(text => ({code: '', args: {}, text}));
  el.innerHTML = `
    <div class="stat-row">${scaleText}
      <div class="stat"><span>${t('res.seat')}</span><b style="font-size:14px">${t('seat.' + h.sitz_aus)}</b></div>
    </div>
    <p class="size-cmp">${t('res.size', {a: mm(h.masse_original_mm), b: mm(h.masse_angepasst_mm)})}</p>
    ${warnings.map(w => `<div class="warn">⚠ ${messageText(w)}</div>`).join('')}
    <div class="res-actions">
      <a class="dl" href="${res.dateien.zip}" download>${res.dateien.teile.length ? t('res.zip.split') : t('res.zip')}</a>
      <button id="install-view" class="mini">${t('res.install')}</button>
    </div>
    <details class="res" open><summary>${t('res.pads', {n: res.dateien.polster.length})}</summary>
      <table class="pads"><tr><th>${t('th.nr')}</th><th>${t('th.part')}</th><th>${t('th.size')}</th><th>${t('th.thick')}</th><th></th></tr>${rows}</table>
      <p class="hint top">${t('res.marks', {m: t('markmode.' + h.markierungen)})}</p>
      <p class="hint">${t('res.print')}</p></details>
    ${wrapRes(splitHtml(res))}
    ${wrapRes(foamHtml(res))}
    ${wrapRes(extrasHtml(res))}
    <details class="res"><summary>${t('mat.h')}</summary>${materialHtml(res)}</details>`;
  $('install-view').onclick = installView;
  bindMaterialInputs();
  el.classList.remove('hidden');
  if (state.dirty) showStale();
}

/* ---------------- Reiter & Anleitungen ---------------- */
// view: welche 3D-Ansicht zum Reiter gehört; 'result' = Ergebnis, sobald eines berechnet ist
const TABS = [
  {id: 'step-helm', view: 'helm'},
  {id: 'step-marks', view: 'helm'},
  {id: 'step-kopf', view: 'kopf'},
  {id: 'step-zonen', view: 'kopf'},
  {id: 'step-params', view: 'result'},
  {id: 'step-split', view: 'result'},
  {id: 'step-run', view: 'result'},
];
const tabLabel = id => t('tab.' + id);

function showTab(id) {
  state.tab = id;
  state.visited.add(id);
  queueMicrotask(updateSteps);
  document.querySelectorAll('#panel > .step').forEach(s => s.classList.toggle('active', s.id === id));
  document.querySelectorAll('#steps button').forEach(b => b.classList.toggle('active', b.dataset.tab === id));
  if (id !== 'step-marks' && state.picking) startPick(null);
  const tab = TABS.find(x => x.id === id);
  const v = tab.view === 'result' ? (state.result ? 'ergebnis' : null) : tab.view;
  if (v && v !== view) setView(v); else updateGizmo();
  $('panel').scrollTop = 0;
}

function buildTabs() {
  let tutOn = false;
  try { tutOn = localStorage.getItem('helmpad.anleitung') === '1'; } catch {}
  TABS.forEach((tab, i) => {
    const b = document.createElement('button');
    b.dataset.tab = tab.id;
    b.innerHTML = `<span class="n">${i + 1}</span><span class="l"></span>`;
    b.onclick = () => showTab(tab.id);
    $('steps').appendChild(b);

    // Anleitung: eigener ?-Knopf je Seite, oder alle über den Schalter oben
    const sec = $(tab.id), h2 = sec.querySelector('h2');
    const help = document.createElement('button');
    help.className = 'help'; help.textContent = '?';
    h2.appendChild(help);
    const box = document.createElement('div');
    box.className = 'tutorial' + (tutOn ? '' : ' hidden');
    h2.after(box);
    help.classList.toggle('on', tutOn);
    help.onclick = () => { box.classList.toggle('hidden'); help.classList.toggle('on', !box.classList.contains('hidden')); };

    const nav = document.createElement('div');
    nav.className = 'stepnav';
    if (i > 0) {
      const back = document.createElement('button');
      back.className = 'back'; back.onclick = () => showTab(TABS[i - 1].id);
      nav.appendChild(back);
    }
    if (i < TABS.length - 1) {
      const next = document.createElement('button');
      next.className = 'next'; next.onclick = () => showTab(TABS[i + 1].id);
      nav.appendChild(next);
    }
    sec.appendChild(nav);
  });
  $('tut-all').checked = tutOn;
  $('tut-all').onchange = e => {
    document.querySelectorAll('.tutorial').forEach(x => x.classList.toggle('hidden', !e.target.checked));
    document.querySelectorAll('.step h2 .help').forEach(x => x.classList.toggle('on', e.target.checked));
    try { localStorage.setItem('helmpad.anleitung', e.target.checked ? '1' : '0'); } catch {}
  };
}

/** Texte der Reiter, Anleitungen und Zurück/Weiter in der aktuellen Sprache. */
function renderTabTexts() {
  TABS.forEach((tab, i) => {
    const b = document.querySelector(`#steps button[data-tab="${tab.id}"]`);
    const sec = $(tab.id), h2 = sec.querySelector('h2'), tut = t('tut.' + tab.id);
    b.querySelector('.l').textContent = tabLabel(tab.id);
    b.title = h2.querySelector('[data-i18n]').textContent;
    h2.querySelector('.help').title = t('help.title');
    sec.querySelector('.tutorial').innerHTML = `<b class="t">${tut.t}</b><ol>${tut.steps.map(s => `<li>${s}</li>`).join('')}</ol>`
      + (tut.tip ? `<p class="tip">${t('tut.tip')} ${tut.tip}</p>` : '');
    const back = sec.querySelector('.stepnav .back'), next = sec.querySelector('.stepnav .next');
    if (back) back.textContent = t('nav.back');
    if (next) next.textContent = t('nav.next', {label: tabLabel(TABS[i + 1].id)});
  });
}

function updateSteps() {
  const done = {
    // Schritt 2 ist optional: erledigt, sobald besucht (auch ohne Punkte)
    'step-helm': !!state.helmGeo, 'step-marks': true, 'step-kopf': !!num('umfang'),
    'step-zonen': state.zones.length > 0, 'step-params': !!state.result, 'step-split': !!state.result,
    'step-run': !!state.result && !state.dirty,
  };
  document.querySelectorAll('#steps button').forEach(b =>
    b.classList.toggle('done', !!done[b.dataset.tab] && !!state.helmGeo && state.visited.has(b.dataset.tab)));
  // fehlende Pflichtangaben am Reiter markieren, sobald man daran vorbei ist
  const missing = new Set(missingSteps().map(m => m.tab));
  const order = TABS.map(x => x.id), here = order.indexOf(state.tab);
  document.querySelectorAll('#steps button').forEach(b => {
    const flag = missing.has(b.dataset.tab) && order.indexOf(b.dataset.tab) < here;
    b.classList.toggle('missing', flag);
    b.title = flag ? t(missingSteps().find(m => m.tab === b.dataset.tab).msg) : b.querySelector('.l').textContent;
  });
}

/* ---------------- Sprache ---------------- */
function refreshTexts() {
  applyStatic();
  renderTabTexts();
  // unveränderte Vorlage: Polsternamen mitübersetzen (eigene Namen bleiben, wie sie sind)
  if (state.preset && state.presets) {
    state.presets[state.preset].zonen.forEach((z, i) => { if (state.zones[i]) state.zones[i].name = zoneName(z.name); });
  }
  renderPresets(); renderZones(); renderMarks(); renderHelmInfo(); updateHelmLock(); renderOrient(); showAngles(); updateRunHint();
  if (state.head) drawHead();
  if (state.helmGeo) drawHelm();
  if (state.picking) startPick(state.picking);
  if (state.result) renderResult(state.result);
}
for (const l of LANGS) $('lang').add(new Option(l.name, l.code));
$('lang').onchange = e => { setLang(e.target.value); refreshTexts(); };

/* ---------------- Projekt speichern / laden ---------------- */
// Gespeichert werden alle Eingaben der Seitenleiste plus Ausrichtung, Punkte und Zonen –
// die Helm-Datei selbst nicht (sie kann groß sein); sie wird beim Laden wieder abgefragt.
const SKIP_INPUTS = new Set(['file', 'proj-file', 'tut-all', 'lang']);
function collectInputs() {
  const values = {};
  document.querySelectorAll('#panel input[id], #panel select[id]').forEach(el => {
    if (SKIP_INPUTS.has(el.id) || el.closest('.zone') || el.closest('.mat-box')) return;
    values[el.id] = el.type === 'checkbox' ? el.checked : el.value;
  });
  document.querySelectorAll('#panel input[type=radio]:checked').forEach(el => { values['radio:' + el.name] = el.value; });
  return values;
}
function applyInputs(values) {
  for (const [k, v] of Object.entries(values || {})) {
    if (k.startsWith('radio:')) {
      const el = document.querySelector(`#panel input[name="${k.slice(6)}"][value="${v}"]`);
      if (el) { el.checked = true; el.dispatchEvent(new Event('change')); }
      continue;
    }
    const el = $(k);
    if (!el || SKIP_INPUTS.has(k)) continue;
    if (el.type === 'checkbox') el.checked = !!v; else el.value = v;
    el.dispatchEvent(new Event(el.type === 'checkbox' || el.tagName === 'SELECT' ? 'change' : 'input'));
  }
}
$('proj-save').onclick = () => {
  const data = {app: 'HelmPad', version: 1, gespeichert: new Date().toISOString(),
    helm: state.helm ? {name: state.helm.name, dreiecke: state.helm.dreiecke} : null,
    rotation: rotationRows(), markierungen: state.marks, zonen: state.zones, vorlage: state.preset, eingaben: collectInputs()};
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
  a.download = `helmpad_${(state.helm?.name || 'projekt').replace(/\.[^.]+$/, '').replace(/[^\w-]+/g, '_')}.json`;
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};
$('proj-load').onclick = () => $('proj-file').click();
$('proj-file').onchange = async e => {
  const f = e.target.files[0]; e.target.value = '';
  if (!f) return;
  try {
    const data = JSON.parse(await f.text());
    if (data.app !== 'HelmPad') throw new Error(t('project.invalid'));
    applyInputs(data.eingaben);
    if (Array.isArray(data.zonen) && data.zonen.length) {
      state.zones = data.zonen; state.preset = data.vorlage ?? null; state.sel = -1;
      renderPresets(); renderZones(); drawHead();
    }
    state.pendingProject = {rotation: data.rotation, marks: data.markierungen || {}, helm: data.helm,
                            einheit: data.eingaben?.einheit};
    if (state.helm && data.helm && state.helm.name === data.helm.name) applyPendingProject();
    else if (data.helm) toast(t('project.need_helm', {name: data.helm.name}));
    loadHead(); markDirty();
  } catch (err) { toast(t('project.invalid') + ' ' + err.message); }
};
/** Ausrichtung und Punkte aus einem geladenen Projekt übernehmen (sobald der passende Helm geladen ist). */
function applyPendingProject() {
  const pend = state.pendingProject;
  if (!pend || !state.helmGeo) return;
  if (pend.einheit) $('einheit').value = pend.einheit;  // sonst überschreibt der automatische Vorschlag sie
  if (pend.rotation) setRotation(pend.rotation);
  state.marks = pend.marks || {};
  state.pendingProject = null;
  marksChanged();
}

/* ---------------- Spenden ---------------- */
// Links in spenden.json eintragen, z.B. "https://paypal.me/NAME" und "https://revolut.me/NAME".
async function setupDonate() {
  let links = {};
  try { links = await (await fetch('spenden.json', {cache: 'no-store'})).json(); } catch {}
  for (const k of ['paypal', 'revolut']) {
    const a = $('donate-' + k), url = (links[k] || '').trim();
    const valid = /^https:\/\//i.test(url);
    a.classList.toggle('disabled', !valid);
    if (valid) a.href = url; else a.removeAttribute('href');
  }
}
const openDonate = on => $('donate').classList.toggle('hidden', !on);
$('donate-btn').onclick = () => openDonate(true);
$('donate-close').onclick = () => openDonate(false);
$('donate').onclick = e => { if (e.target.id === 'donate') openDonate(false); };
addEventListener('keydown', e => { if (e.key === 'Escape') openDonate(false); });

/* ---------------- Start ---------------- */
setLang(detectLang());
$('lang').value = getLang();
applyStatic();
resize();
buildTabs();
renderTabTexts();
renderMarks();
updateHelmLock();
setView('helm');
showTab('step-helm');
setupDonate();
loadPresets().then(loadHead).catch(e => toast(e.message));
markDirty();

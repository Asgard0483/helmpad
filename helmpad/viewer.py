"""Eigenständige HTML-Vorschau (three.js) mit eingebetteten Meshes."""
import base64
import json

import trimesh

MAX_VIEW_FACES = 120_000

PAD_COLORS = ["#e4572e", "#29a3a3", "#f3a712", "#7768ae", "#4c9f38", "#d1495b", "#3b8ea5", "#c17c74"]


def _b64(mesh):
    if len(mesh.faces) > MAX_VIEW_FACES:
        mesh = mesh.simplify_quadric_decimation(face_count=MAX_VIEW_FACES)
    return base64.b64encode(mesh.export(file_type="stl")).decode()


def write_viewer(path, head, helmet, pads, title="HelmPad Vorschau"):
    items = [
        {"name": "Kopf", "color": "#d9b38c", "opacity": 1.0, "data": _b64(head)},
        {"name": "Helm", "color": "#9aa4ad", "opacity": 0.35, "data": _b64(helmet)},
    ]
    for i, pad in enumerate(pads):
        items.append({"name": pad.zone.name, "color": PAD_COLORS[i % len(PAD_COLORS)],
                      "opacity": 1.0, "data": _b64(pad.mesh)})
    # "</" maskieren, damit kein Name den <script>-Block beenden kann
    html = TEMPLATE.replace("__TITLE__", title).replace("__ITEMS__", json.dumps(items).replace("</", "<\\/"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


TEMPLATE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  html,body{margin:0;height:100%;background:#1d2126;color:#e8eaed;font:14px system-ui,sans-serif;overflow:hidden}
  #ui{position:absolute;top:12px;left:12px;background:#2a2f36e6;padding:10px 14px;border-radius:8px;max-width:calc(100vw - 52px)}
  #ui h1{font-size:15px;margin:0 0 8px}
  label{display:flex;align-items:center;gap:6px;margin:3px 0;cursor:pointer}
  .sw{width:12px;height:12px;border-radius:3px;display:inline-block}
  #hint{position:absolute;bottom:10px;left:12px;opacity:.6;font-size:12px}
</style>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
"three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
</head><body>
<div id="ui"><h1>__TITLE__</h1><div id="list"></div>
<label><input type="checkbox" id="cut"> Schnitt (rechte Hälfte ausblenden)</label></div>
<div id="hint">Ziehen = drehen · Rechtsklick = verschieben · Mausrad = zoomen</div>
<script type="module">
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {STLLoader} from 'three/addons/loaders/STLLoader.js';
const items = __ITEMS__;
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setPixelRatio(devicePixelRatio); renderer.setSize(innerWidth, innerHeight);
renderer.localClippingEnabled = true;
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene(); scene.background = new THREE.Color('#1d2126');
const camera = new THREE.PerspectiveCamera(40, innerWidth/innerHeight, 1, 5000);
camera.up.set(0,0,1); camera.position.set(420,520,260);
const controls = new OrbitControls(camera, renderer.domElement); controls.target.set(0,0,30);
scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.8); sun.position.set(300,400,500); scene.add(sun);
const clip = new THREE.Plane(new THREE.Vector3(-1,0,0), 0);
const loader = new STLLoader(); const list = document.getElementById('list'); const mats = [];
for (const it of items) {
  const bin = Uint8Array.from(atob(it.data), c => c.charCodeAt(0)).buffer;
  const geo = loader.parse(bin); geo.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({color: it.color, roughness:.6, metalness:.05,
    transparent: it.opacity < 1, opacity: it.opacity, side: THREE.DoubleSide, depthWrite: it.opacity >= 1});
  mats.push(mat);
  const mesh = new THREE.Mesh(geo, mat); scene.add(mesh);
  const lab = document.createElement('label');
  lab.innerHTML = `<input type="checkbox" checked><span class="sw" style="background:${it.color}"></span>`;
  lab.append(it.name);
  lab.querySelector('input').onchange = e => { mesh.visible = e.target.checked; };
  list.appendChild(lab);
}
document.getElementById('cut').onchange = e => { for (const m of mats) m.clippingPlanes = e.target.checked ? [clip] : []; };
addEventListener('resize', () => { camera.aspect = innerWidth/innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight); });
renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });
</script></body></html>
"""

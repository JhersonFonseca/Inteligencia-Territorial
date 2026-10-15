// Mapa del módulo territorial. Sin compilación: MapLibre GL desde CDN.
"use strict";

const ZOOM_DETALLE = 15;
const CAPAS_DETALLE = ["manzana", "estrato_2025", "estrato_2019"];
const ORDEN_CLIC = ["manzana", "estrato_2025", "estrato_2019", "sector", "upl", "localidad"];
const NIVEL_DE_CAPA = { estrato_2025: "manzana", estrato_2019: "manzana" };
const ESTILO_LINEA = {
  localidad: { color: "#1d1d1b", ancho: 2.2 },
  upl: { color: "#0b5cad", ancho: 1.6 },
  sector: { color: "#6b6b66", ancho: 0.8 },
  manzana: { color: "#9a9a94", ancho: 0.5 },
};
// Colores por categoría de estrato. Son solo categorías visuales: no expresan ingresos.
const COLOR_ESTRATO = ["match", ["get", "estrato"],
  1, "#fde0c5", 2, "#facba6", 3, "#f8b58b", 4, "#f59e72", 5, "#f2855d", 6, "#ef6a4c", "#c8c8c3"];

const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (n) => (n == null ? "—" : Number(n).toLocaleString("es-CO"));

async function api(ruta) {
  const r = await fetch(ruta);
  const cuerpo = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new Error(cuerpo.detalle || cuerpo.detail || `Error ${r.status}`);
    e.estado = r.status; e.cuerpo = cuerpo; throw e;
  }
  return cuerpo;
}

const mapa = new maplibregl.Map({
  container: "mapa",
  style: {
    version: 8,
    sources: {
      base: {
        type: "raster", tileSize: 256,
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        attribution: "Mapa base © colaboradores de OpenStreetMap",
      },
    },
    layers: [{ id: "base", type: "raster", source: "base" }],
  },
  center: [-74.1, 4.65], zoom: 10.3, maxBounds: [[-74.75, 3.5], [-73.7, 5.05]],
});
mapa.addControl(new maplibregl.NavigationControl(), "top-right");

const capas = {};      // capa -> metadatos de /api/capas
const visibles = new Set(["localidad"]);

function agregarCapa(c) {
  const id = c.capa;
  mapa.addSource(id, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  if (id.startsWith("estrato")) {
    mapa.addLayer({ id: `${id}-relleno`, type: "fill", source: id,
      paint: { "fill-color": COLOR_ESTRATO, "fill-opacity": 0.65 }, layout: { visibility: "none" } });
  } else {
    mapa.addLayer({ id: `${id}-relleno`, type: "fill", source: id,
      paint: { "fill-color": "#0b5cad", "fill-opacity": 0.01 }, layout: { visibility: "none" } });
    const e = ESTILO_LINEA[id];
    mapa.addLayer({ id: `${id}-linea`, type: "line", source: id,
      paint: { "line-color": e.color, "line-width": e.ancho }, layout: { visibility: "none" } });
  }
}

function idsCapa(id) { return [`${id}-relleno`, `${id}-linea`].filter((x) => mapa.getLayer(x)); }

async function refrescarCapa(id) {
  const c = capas[id];
  if (!c || !c.habilitada) return;
  const mostrar = visibles.has(id) && (!CAPAS_DETALLE.includes(id) || mapa.getZoom() >= ZOOM_DETALLE);
  for (const l of idsCapa(id)) mapa.setLayoutProperty(l, "visibility", mostrar ? "visible" : "none");
  if (!mostrar) return;
  let ruta = `/api/capas/${id}/geojson`;
  if (CAPAS_DETALLE.includes(id)) {
    const b = mapa.getBounds();
    ruta += `?bbox=${[b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((x) => x.toFixed(5)).join(",")}`;
  } else if (c.cargada) {
    return;
  }
  try {
    const datos = await api(ruta);
    mapa.getSource(id).setData(datos);
    c.cargada = true;
    if (datos.fuente && datos.fuente.sintetico) document.getElementById("aviso-sintetico").hidden = false;
  } catch (e) {
    console.warn(`No se pudo cargar ${id}:`, e.message);
  }
}

function refrescarTodo() { Object.keys(capas).forEach(refrescarCapa); }

function pintarPanelCapas(lista) {
  const cont = document.getElementById("capas");
  const noHab = document.getElementById("no-habilitadas");
  cont.innerHTML = ""; noHab.innerHTML = "";
  for (const c of lista) {
    const etiqueta = document.createElement("label");
    etiqueta.className = "capa" + (c.habilitada ? "" : " deshabilitada");
    const caja = document.createElement("input");
    caja.type = "checkbox"; caja.disabled = !c.habilitada; caja.checked = c.habilitada && visibles.has(c.capa);
    caja.addEventListener("change", () => {
      caja.checked ? visibles.add(c.capa) : visibles.delete(c.capa);
      refrescarCapa(c.capa);
      if (!caja.checked) for (const l of idsCapa(c.capa)) mapa.setLayoutProperty(l, "visibility", "none");
    });
    const texto = document.createElement("span");
    texto.innerHTML = `${esc(c.nombre || c.capa)}${c.habilitada ? "" : " <em>(no validada)</em>"}`;
    etiqueta.append(caja, texto);
    cont.append(etiqueta);
    if (!c.habilitada) {
      const li = document.createElement("li");
      li.innerHTML = `<strong>${esc(c.nombre || c.fuente)}</strong>: ${c.motivos.map(esc).join(" ")}`;
      noHab.append(li);
    }
    if (c.sintetico) document.getElementById("aviso-sintetico").hidden = false;
  }
  if (!noHab.children.length) noHab.innerHTML = "<li>Ninguna.</li>";
}

// --- ficha ------------------------------------------------------------------------------------
function fuenteHtml(f, titulo = "Fuente") {
  if (!f) return "";
  return `<details><summary>${esc(titulo)}: ${esc(f.nombre)}</summary>
    <div>Entidad: ${esc(f.entidad)}</div>
    <div>Fecha del dato: ${esc(f.fecha_dato || "desconocida")} <span class="meta">(${esc(f.fecha_dato_origen || "")})</span></div>
    <div>Licencia: ${esc(f.licencia || "no verificada")}</div>
    <div>Versión publicada: ${esc(f.version_publicada)} · cargada ${esc((f.publicada_en || "").slice(0, 16).replace("T", " "))}</div>
    ${f.sintetico ? '<div class="aviso aviso-sintetico">Versión sintética</div>' : ""}
    <div><a href="${esc(f.url)}" target="_blank" rel="noopener">Servicio o archivo</a>
    ${f.ficha_url ? ` · <a href="${esc(f.ficha_url)}" target="_blank" rel="noopener">Ficha del conjunto</a>` : ""}</div>
  </details>`;
}

let advertenciasGenerales = [];

function bloqueHtml(b) {
  if (b.estado === "no_validada") {
    return `<section class="bloque no-validada"><h3>${esc(b.titulo)}</h3>
      <p><strong>Capa no validada: no se muestran datos.</strong></p>
      <ul>${b.motivos.map((m) => `<li>${esc(m)}</li>`).join("")}</ul></section>`;
  }
  const filas = (b.distribucion || []).map((d) => `<tr><td>${esc(d.etiqueta)}</td>
      <td class="num">${num(d.n)}</td><td class="num">${d.porcentaje == null ? "—" : d.porcentaje.toLocaleString("es-CO") + " %"}</td>
      <td style="width:35%"><div class="barra" style="width:${d.porcentaje || 0}%"></div></td></tr>`).join("");
  const extra = b.manzanas_sin_coincidencia
    ? `<p class="aviso">Manzanas de la unidad sin estrato asignado en esta fuente: ${num(b.manzanas_sin_coincidencia)}.</p>` : "";
  return `<section class="bloque"><h3>${esc(b.titulo)}</h3>
    ${b.estado === "sin_datos" ? '<p class="aviso">La fuente está habilitada, pero no hay registros que coincidan con esta unidad.</p>' : ""}
    <table><thead><tr><th>Categoría</th><th class="num">${esc(b.unidad_medida)}</th><th class="num">%</th><th></th></tr></thead>
      <tbody>${filas}</tbody>
      <tfoot><tr><th>Total</th><th class="num">${num(b.total)}</th><th></th><th></th></tr></tfoot></table>
    ${extra}
    ${(b.advertencias || []).filter((a) => !advertenciasGenerales.includes(a)).map((a) => `<p class="aviso">${esc(a)}</p>`).join("")}
    <p class="meta">Método: ${esc(b.metodo)}</p>
    ${fuenteHtml(b.fuente)}${fuenteHtml(b.fuente_relacion, "Relación")}
  </section>`;
}

async function abrirFicha(nivel, codigo) {
  const panel = document.getElementById("panel-ficha");
  panel.innerHTML = '<p class="vacio">Cargando…</p>';
  try {
    const f = await api(`/api/ficha/${encodeURIComponent(nivel)}/${encodeURIComponent(codigo)}`);
    if (f.contiene_datos_sinteticos) document.getElementById("aviso-sintetico").hidden = false;
    advertenciasGenerales = f.advertencias_generales;
    panel.innerHTML = `
      ${f.contiene_datos_sinteticos ? '<p class="aviso aviso-sintetico">Esta ficha contiene datos sintéticos de prueba.</p>' : ""}
      <h2>${esc(nivel)}</h2>
      <h3 style="font-size:18px">${esc(f.nombre || f.codigo)}</h3>
      <p class="meta">Código ${esc(f.codigo)}${f.padre ? ` · pertenece a ${esc(f.padre)}` : ""} ·
        ${num(f.area_ha)} ha · ${num(f.manzanas_en_unidad)} manzanas</p>
      ${f.advertencias_generales.map((a) => `<p class="aviso">${esc(a)}</p>`).join("")}
      ${fuenteHtml(f.fuente_unidad, "Límite")}
      ${f.bloques.map(bloqueHtml).join("")}`;
    if (f.caja && f.caja.coordinates) {
      const xs = f.caja.coordinates[0].map((p) => p[0]); const ys = f.caja.coordinates[0].map((p) => p[1]);
      mapa.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]], { padding: 40, maxZoom: 17 });
    }
  } catch (e) {
    const motivos = (e.cuerpo && e.cuerpo.motivos) || [];
    panel.innerHTML = `<p class="aviso no-validada">${esc(e.message)}</p>
      <ul>${motivos.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>`;
  }
}

mapa.on("click", (ev) => {
  const capasActivas = ORDEN_CLIC.filter((id) => mapa.getLayer(`${id}-relleno`) &&
    mapa.getLayoutProperty(`${id}-relleno`, "visibility") === "visible");
  for (const id of capasActivas) {
    const f = mapa.queryRenderedFeatures(ev.point, { layers: [`${id}-relleno`] })[0];
    if (f) { abrirFicha(NIVEL_DE_CAPA[id] || id, f.properties.codigo); return; }
  }
});

// --- buscador ---------------------------------------------------------------------------------
const q = document.getElementById("q");
const lista = document.getElementById("resultados");
let temporizador;
q.addEventListener("input", () => {
  clearTimeout(temporizador);
  temporizador = setTimeout(async () => {
    if (q.value.trim().length < 2) { lista.hidden = true; return; }
    try {
      const { resultados } = await api(`/api/buscar?q=${encodeURIComponent(q.value.trim())}`);
      lista.innerHTML = resultados.length
        ? resultados.map((r) => `<li data-nivel="${esc(r.nivel)}" data-codigo="${esc(r.codigo)}">
            <strong>${esc(r.nombre || r.codigo)}</strong> <span class="meta">${esc(r.nivel)} ${esc(r.codigo)}</span></li>`).join("")
        : '<li class="meta">Sin resultados en las fuentes habilitadas.</li>';
      lista.hidden = false;
    } catch { lista.hidden = true; }
  }, 250);
});
lista.addEventListener("click", (ev) => {
  const li = ev.target.closest("li[data-nivel]");
  if (!li) return;
  lista.hidden = true; q.value = "";
  abrirFicha(li.dataset.nivel, li.dataset.codigo);
});
document.getElementById("buscador").addEventListener("submit", (ev) => ev.preventDefault());

// --- inicio -----------------------------------------------------------------------------------
mapa.on("load", async () => {
  try {
    const { capas: lista } = await api("/api/capas");
    for (const c of lista) { capas[c.capa] = c; agregarCapa(c); }
    pintarPanelCapas(lista);
    refrescarTodo();
  } catch (e) {
    document.getElementById("panel-ficha").innerHTML =
      `<p class="aviso no-validada">No se pudo consultar la API: ${esc(e.message)}</p>`;
  }
});
mapa.on("moveend", () => CAPAS_DETALLE.forEach(refrescarCapa));

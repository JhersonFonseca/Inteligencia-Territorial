// Mapa del módulo territorial. Sin compilación. MapLibre GL se sirve desde /web/vendor.
//
// El mapa base (teselas de OpenStreetMap) es solo contexto: si no responde, las capas territoriales
// se siguen dibujando sobre un fondo neutro y se muestra un aviso con el motivo.
// El estado completo queda en window.estadoMapa para las verificaciones automáticas.
"use strict";

const ZOOM_DETALLE = 15;
const ESPERA_MAPA_BASE_MS = 8000;
const CAPAS_DETALLE = ["manzana", "estrato_2025", "estrato_2019"];
const ORDEN_CLIC = ["manzana", "estrato_2025", "estrato_2019", "sector", "upl", "localidad"];
const NIVEL_DE_CAPA = { estrato_2025: "manzana", estrato_2019: "manzana" };
const ESTILO = {
  localidad: { linea: "#1d1d1b", ancho: 2.2, relleno: 0.06 },
  upl: { linea: "#0b5cad", ancho: 1.6, relleno: 0.05 },
  sector: { linea: "#55554f", ancho: 0.9, relleno: 0.04 },
  manzana: { linea: "#7d7d77", ancho: 0.6, relleno: 0.10 },
};
const COLOR_SELECCION = "#d4380d";
// Colores por categoría de estrato. Son solo categorías visuales: no expresan ingresos.
const COLOR_ESTRATO = ["match", ["get", "estrato"],
  1, "#fde0c5", 2, "#facba6", 3, "#f8b58b", 4, "#f59e72", 5, "#f2855d", 6, "#ef6a4c", "#c8c8c3"];

const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (n) => (n == null ? "—" : Number(n).toLocaleString("es-CO"));
const $ = (id) => document.getElementById(id);
const plural = (n, uno, varios) => `${num(n)} ${n === 1 ? uno : varios}`;

// --- estado observable ------------------------------------------------------------------------
const estado = {
  motor: { estado: "cargando", detalle: "" },
  base: { estado: "cargando", teselasOk: 0, teselasFallidas: 0, ultimoError: "" },
  capas: {},           // capa -> {nombre, habilitada, visible, estado, n, sintetico, truncada, detalle}
  zoom: null,
  seleccion: null,     // {nivel, codigo, nombre, sintetico}
  sinteticas: [],
  errores: [],         // errores reales (no incluye el mapa base, que se resume aparte)
  listo: false,
};
window.estadoMapa = estado;

async function api(ruta) {
  const r = await fetch(ruta);
  const cuerpo = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new Error(cuerpo.detalle || cuerpo.detail || `Error ${r.status}`);
    e.estado = r.status; e.cuerpo = cuerpo; throw e;
  }
  return cuerpo;
}

function registrarError(origen, e) {
  estado.errores.push({ origen, mensaje: String(e && e.message || e) });
  console.error(`[mapa] ${origen}:`, e);
}

// --- avisos -----------------------------------------------------------------------------------
function pintarAvisoMapaBase() {
  const b = estado.base, el = $("aviso-mapa-base");
  const textos = {
    no_disponible: `<strong>Mapa base no disponible.</strong> No se pudo cargar ninguna tesela de
      OpenStreetMap (${plural(b.teselasFallidas, "intento fallido", "intentos fallidos")}). Las capas territoriales se muestran
      sobre un fondo neutro y siguen funcionando.`,
    sin_respuesta: `<strong>El mapa base no responde.</strong> OpenStreetMap no ha entregado teselas
      después de ${ESPERA_MAPA_BASE_MS / 1000} segundos. Las capas territoriales se muestran sobre un
      fondo neutro.`,
    parcial: `<strong>Mapa base incompleto.</strong> ${plural(b.teselasFallidas, "tesela no cargó", "teselas no cargaron")};
      pueden verse zonas vacías en el fondo.`,
  };
  el.innerHTML = textos[b.estado] || "";
  el.hidden = !textos[b.estado];
}

function pintarAvisoSintetico() {
  const sint = new Set(Object.entries(estado.capas).filter(([, c]) => c.sintetico).map(([, c]) => c.nombre));
  if (estado.seleccion && estado.seleccion.sintetico) sint.add("ficha seleccionada");
  estado.sinteticas = [...sint];
  $("aviso-sintetico").hidden = sint.size === 0;
  $("aviso-sintetico-capas").textContent = sint.size ? `Afecta a: ${[...sint].join(", ")}.` : "";
}

function pintarEstado() {
  const items = [];
  const li = (clase, texto) => items.push(`<li class="${clase}"><span class="punto"></span><span>${texto}</span></li>`);
  const m = estado.motor;
  li(m.estado === "listo" ? "ok" : m.estado === "error" ? "error" : "", `Motor del mapa: ${esc(m.detalle || m.estado)}`);
  const b = estado.base;
  const base = {
    cargando: ["", "cargando…"], disponible: ["ok", "disponible"],
    parcial: ["aviso-e", `incompleto (${plural(b.teselasFallidas, "tesela fallida", "teselas fallidas")})`],
    no_disponible: ["error", `no disponible (${plural(b.teselasFallidas, "tesela fallida", "teselas fallidas")})`],
    sin_respuesta: ["error", "sin respuesta"],
  }[b.estado] || ["", b.estado];
  li(base[0], `Mapa base OpenStreetMap: ${base[1]}`);
  if (estado.zoom != null) li("", `Zoom: ${estado.zoom.toFixed(1)}`);
  for (const [id, c] of Object.entries(estado.capas)) {
    if (!c.habilitada || !c.visible) continue;
    const sint = c.sintetico ? ' · <span class="sint">SINTÉTICA</span>' : c.estado === "cargada" ? " · datos oficiales" : "";
    const textos = {
      requiere_zoom: ["aviso-e", `acerca el mapa (zoom ${ZOOM_DETALLE} o más)`],
      cargando: ["", "cargando…"],
      cargada: [c.n ? "ok" : "aviso-e", c.n ? `${plural(c.n, "polígono", "polígonos")}${c.truncada ? " (vista recortada: acerca más)" : ""}` : "sin polígonos en esta vista"],
      error: ["error", `error: ${esc(c.detalle)}`],
    }[c.estado] || ["", c.estado];
    li(textos[0], `${esc(c.nombre)}: ${textos[1]}${sint}`);
  }
  for (const e of estado.errores.slice(-3)) li("error", `${esc(e.origen)}: ${esc(e.mensaje)}`);
  $("estado-mapa").innerHTML = items.join("");
}

// --- motor del mapa ---------------------------------------------------------------------------
let mapa = null;
if (typeof maplibregl === "undefined") {
  estado.motor = { estado: "error", detalle: "no se pudo cargar MapLibre" };
  const el = $("aviso-mapa-error");
  el.innerHTML = "<strong>No se pudo iniciar el mapa.</strong> El archivo de MapLibre no cargó. " +
    "El buscador y las fichas siguen disponibles.";
  el.hidden = false;
} else {
  try {
    mapa = new maplibregl.Map({
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
        layers: [
          // Fondo neutro: es lo que se ve cuando el mapa base no está disponible.
          { id: "fondo", type: "background", paint: { "background-color": "#e9ebe6" } },
          { id: "base", type: "raster", source: "base" },
        ],
      },
      center: [-74.1, 4.65], zoom: 10.3, maxBounds: [[-74.75, 3.5], [-73.7, 5.05]],
    });
    mapa.addControl(new maplibregl.NavigationControl(), "top-right");
    mapa.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    window.mapaTerritorial = mapa;
  } catch (e) {
    mapa = null;
    estado.motor = { estado: "error", detalle: "no se pudo crear el mapa (WebGL)" };
    const el = $("aviso-mapa-error");
    el.innerHTML = `<strong>No se pudo iniciar el mapa.</strong> ${esc(e.message)}. ` +
      "Revisa que el navegador tenga WebGL activo. El buscador y las fichas siguen disponibles.";
    el.hidden = false;
    registrarError("motor del mapa", e);
  }
}

// --- mapa base: seguimiento de teselas ---------------------------------------------------------
function evaluarMapaBase() {
  const b = estado.base;
  const anterior = b.estado;
  if (b.teselasOk > 0) b.estado = b.teselasFallidas > 0 ? "parcial" : "disponible";
  else if (b.teselasFallidas > 0) b.estado = "no_disponible";
  if (b.estado !== anterior) {
    if (b.estado === "no_disponible" || b.estado === "parcial") {
      console.warn(`[mapa] Mapa base ${b.estado}: ${b.teselasFallidas} teselas fallidas. Último error: ${b.ultimoError}`);
    }
  }
  pintarAvisoMapaBase(); pintarEstado();
}

if (mapa) {
  mapa.on("sourcedata", (e) => {
    if (e.sourceId === "base" && e.tile && e.dataType === "source") { estado.base.teselasOk += 1; evaluarMapaBase(); }
  });
  // Con un manejador registrado, MapLibre no imprime cada tesela fallida: se resume en un aviso.
  mapa.on("error", (e) => {
    if (e.sourceId === "base" || (e.source && e.source.id === "base") || (e.tile && !e.sourceId)) {
      estado.base.teselasFallidas += 1;
      estado.base.ultimoError = String(e.error && e.error.message || e.error || "");
      evaluarMapaBase();
    } else {
      registrarError("motor del mapa", e.error || e);
      pintarEstado();
    }
  });
  setTimeout(() => {
    if (estado.base.estado === "cargando") {
      estado.base.estado = "sin_respuesta";
      console.warn(`[mapa] El mapa base no respondió en ${ESPERA_MAPA_BASE_MS / 1000} s.`);
      pintarAvisoMapaBase(); pintarEstado();
    }
  }, ESPERA_MAPA_BASE_MS);
}

// --- capas territoriales ----------------------------------------------------------------------
const metadatos = {};          // capa -> metadatos de /api/capas
const visibles = new Set(["localidad"]);
const secuencia = {};          // evita que una respuesta antigua sobrescriba una nueva

function agregarCapa(c) {
  const id = c.capa;
  mapa.addSource(id, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  const oculto = { visibility: "none" };
  if (id.startsWith("estrato")) {
    mapa.addLayer({ id: `${id}-relleno`, type: "fill", source: id, layout: oculto,
      paint: { "fill-color": COLOR_ESTRATO, "fill-opacity": 0.7, "fill-outline-color": "#ffffff" } });
  } else {
    const e = ESTILO[id];
    mapa.addLayer({ id: `${id}-relleno`, type: "fill", source: id, layout: oculto,
      paint: { "fill-color": "#0b5cad", "fill-opacity": e.relleno } });
    mapa.addLayer({ id: `${id}-linea`, type: "line", source: id, layout: oculto,
      paint: { "line-color": e.linea, "line-width": e.ancho } });
  }
  // Resaltado de la unidad seleccionada
  mapa.addLayer({ id: `${id}-seleccion`, type: "line", source: id, layout: oculto,
    filter: ["==", ["get", "codigo"], "__ninguna__"],
    paint: { "line-color": COLOR_SELECCION, "line-width": 3.5 } });
}

function idsCapa(id) { return [`${id}-relleno`, `${id}-linea`, `${id}-seleccion`].filter((x) => mapa.getLayer(x)); }

async function refrescarCapa(id) {
  const c = metadatos[id], st = estado.capas[id];
  if (!mapa || !c || !c.habilitada) return;
  st.visible = visibles.has(id);
  const zoomSuficiente = !CAPAS_DETALLE.includes(id) || mapa.getZoom() >= ZOOM_DETALLE;
  const mostrar = st.visible && zoomSuficiente;
  for (const l of idsCapa(id)) mapa.setLayoutProperty(l, "visibility", mostrar ? "visible" : "none");
  if (!st.visible) { pintarEstado(); return; }
  if (!zoomSuficiente) { st.estado = "requiere_zoom"; pintarEstado(); return; }
  let ruta = `/api/capas/${id}/geojson`;
  if (CAPAS_DETALLE.includes(id)) {
    const b = mapa.getBounds();
    ruta += `?bbox=${[b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((x) => x.toFixed(5)).join(",")}`;
  } else if (st.estado === "cargada") {
    pintarEstado(); return;
  }
  const turno = (secuencia[id] = (secuencia[id] || 0) + 1);
  st.estado = "cargando"; pintarEstado();
  try {
    const datos = await api(ruta);
    if (turno !== secuencia[id]) return;
    mapa.getSource(id).setData(datos);
    Object.assign(st, { estado: "cargada", n: datos.features.length, truncada: !!datos.truncada,
                        sintetico: !!(datos.fuente && datos.fuente.sintetico), detalle: "" });
  } catch (e) {
    if (turno !== secuencia[id]) return;
    Object.assign(st, { estado: "error", detalle: e.message });
    registrarError(`capa ${c.nombre}`, e);
  }
  pintarAvisoSintetico(); pintarEstado();
}

function refrescarTodo() { return Promise.all(Object.keys(metadatos).map(refrescarCapa)); }

function asegurarVisible(id) {
  if (!metadatos[id] || !metadatos[id].habilitada || visibles.has(id)) return;
  visibles.add(id);
  const caja = document.querySelector(`input[data-capa="${id}"]`);
  if (caja) caja.checked = true;
}

function pintarPanelCapas(lista) {
  const cont = $("capas"), noHab = $("no-habilitadas");
  cont.innerHTML = ""; noHab.innerHTML = "";
  for (const c of lista) {
    const etiqueta = document.createElement("label");
    etiqueta.className = "capa" + (c.habilitada ? "" : " deshabilitada");
    const caja = document.createElement("input");
    caja.type = "checkbox"; caja.dataset.capa = c.capa;
    caja.disabled = !c.habilitada || !mapa; caja.checked = c.habilitada && visibles.has(c.capa);
    caja.addEventListener("change", () => {
      caja.checked ? visibles.add(c.capa) : visibles.delete(c.capa);
      refrescarCapa(c.capa);
    });
    const texto = document.createElement("span");
    texto.innerHTML = `${esc(c.nombre || c.capa)}${c.habilitada ? "" : " <em>(no validada)</em>"}` +
      (CAPAS_DETALLE.includes(c.capa) && c.habilitada ? ` <span class="meta">(zoom ${ZOOM_DETALLE}+)</span>` : "");
    etiqueta.append(caja, texto);
    cont.append(etiqueta);
    if (!c.habilitada) {
      const li = document.createElement("li");
      li.innerHTML = `<strong>${esc(c.nombre || c.fuente)}</strong>: ${c.motivos.map(esc).join(" ")}`;
      noHab.append(li);
    }
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

function resaltar(nivel, codigo) {
  if (!mapa) return;
  for (const id of Object.keys(metadatos)) {
    if (!mapa.getLayer(`${id}-seleccion`)) continue;
    const propio = id === nivel;
    mapa.setFilter(`${id}-seleccion`, ["==", ["get", "codigo"], propio ? codigo : "__ninguna__"]);
  }
}

async function abrirFicha(nivel, codigo, { encuadrar = true } = {}) {
  const panel = $("panel-ficha");
  panel.innerHTML = '<p class="vacio">Cargando…</p>';
  panel.dataset.estado = "cargando";
  try {
    const f = await api(`/api/ficha/${encodeURIComponent(nivel)}/${encodeURIComponent(codigo)}`);
    advertenciasGenerales = f.advertencias_generales;
    estado.seleccion = { nivel, codigo: f.codigo, nombre: f.nombre, sintetico: f.contiene_datos_sinteticos };
    panel.innerHTML = `
      ${f.contiene_datos_sinteticos ? '<p class="aviso aviso-sintetico">Esta ficha contiene datos sintéticos de prueba.</p>' : ""}
      <h2>${esc(nivel)}</h2>
      <h3 id="ficha-titulo" style="font-size:18px">${esc(f.nombre || f.codigo)}</h3>
      <p class="meta">Código <span id="ficha-codigo">${esc(f.codigo)}</span>${f.padre ? ` · pertenece a ${esc(f.padre)}` : ""} ·
        ${num(f.area_ha)} ha · ${num(f.manzanas_en_unidad)} manzanas</p>
      ${f.advertencias_generales.map((a) => `<p class="aviso">${esc(a)}</p>`).join("")}
      ${fuenteHtml(f.fuente_unidad, "Límite")}
      ${f.bloques.map(bloqueHtml).join("")}`;
    panel.dataset.estado = "lista";
    pintarAvisoSintetico();
    if (mapa) {
      asegurarVisible(nivel);
      resaltar(nivel, f.codigo);
      if (encuadrar && f.caja && f.caja.coordinates) {
        const xs = f.caja.coordinates[0].map((p) => p[0]); const ys = f.caja.coordinates[0].map((p) => p[1]);
        mapa.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]],
                       { padding: 60, maxZoom: 17, duration: 600 });
      }
      refrescarCapa(nivel);
    }
  } catch (e) {
    const motivos = (e.cuerpo && e.cuerpo.motivos) || [];
    panel.innerHTML = `<p class="aviso no-validada">${esc(e.message)}</p>
      <ul>${motivos.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>`;
    panel.dataset.estado = "error";
  }
}

if (mapa) {
  mapa.on("click", (ev) => {
    const activas = ORDEN_CLIC.filter((id) => mapa.getLayer(`${id}-relleno`) &&
      mapa.getLayoutProperty(`${id}-relleno`, "visibility") === "visible");
    for (const id of activas) {
      const f = mapa.queryRenderedFeatures(ev.point, { layers: [`${id}-relleno`] })[0];
      if (f) { abrirFicha(NIVEL_DE_CAPA[id] || id, f.properties.codigo, { encuadrar: false }); return; }
    }
  });
  for (const id of ORDEN_CLIC) {
    mapa.on("mouseenter", `${id}-relleno`, () => { mapa.getCanvas().style.cursor = "pointer"; });
    mapa.on("mouseleave", `${id}-relleno`, () => { mapa.getCanvas().style.cursor = ""; });
  }
  mapa.on("zoomend", () => { estado.zoom = mapa.getZoom(); pintarEstado(); });
  mapa.on("moveend", () => CAPAS_DETALLE.forEach(refrescarCapa));
}

// --- buscador ---------------------------------------------------------------------------------
const q = $("q"), lista = $("resultados");
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
    } catch (e) { lista.hidden = true; registrarError("buscador", e); pintarEstado(); }
  }, 250);
});
lista.addEventListener("click", (ev) => {
  const li = ev.target.closest("li[data-nivel]");
  if (!li) return;
  lista.hidden = true; q.value = "";
  abrirFicha(li.dataset.nivel, li.dataset.codigo);
});
$("buscador").addEventListener("submit", (ev) => ev.preventDefault());

// --- inicio -----------------------------------------------------------------------------------
async function iniciarCapas() {
  try {
    const { capas: lista } = await api("/api/capas");
    for (const c of lista) {
      metadatos[c.capa] = c;
      estado.capas[c.capa] = { nombre: c.nombre || c.capa, habilitada: c.habilitada, visible: false,
                               estado: c.habilitada ? "oculta" : "no_habilitada", n: 0,
                               sintetico: !!c.sintetico, truncada: false, detalle: "" };
      if (mapa) agregarCapa(c);
    }
    pintarPanelCapas(lista);
    pintarAvisoSintetico();
    if (mapa) await refrescarTodo();
  } catch (e) {
    registrarError("API de capas", e);
    $("panel-ficha").innerHTML = `<p class="aviso no-validada">No se pudo consultar la API: ${esc(e.message)}</p>`;
  }
  estado.listo = true;
  pintarEstado();
}

// Las capas se agregan en cuanto el estilo está listo ("style.load"), sin esperar a "load":
// MapLibre solo emite "load" cuando terminan de llegar las teselas iniciales, y si el proveedor del
// mapa base no responde, ese evento no llega nunca y el mapa quedaría vacío. (Tampoco sirve
// isStyleLoaded(): devuelve falso mientras haya teselas pendientes.)
let arrancado = false;
function arrancar() {
  if (arrancado) return;
  arrancado = true;
  estado.motor = { estado: "listo", detalle: `MapLibre ${maplibregl.getVersion()} listo` };
  estado.zoom = mapa.getZoom();
  iniciarCapas();
}
if (mapa) {
  mapa.on("style.load", arrancar);
  mapa.on("load", arrancar);
} else {
  iniciarCapas();
}
pintarEstado();

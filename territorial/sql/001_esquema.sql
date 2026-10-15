-- Esquema del módulo territorial. Idempotente: se puede ejecutar varias veces.
-- No contiene ninguna operación que borre datos.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS geo;

-- Registro de fuentes (se sincroniza desde territorial/fuentes.yaml)
CREATE TABLE IF NOT EXISTS geo.fuente (
  fuente_id            text PRIMARY KEY,
  nombre               text NOT NULL,
  nivel                text NOT NULL,
  entidad              text NOT NULL,
  tipo                 text NOT NULL,
  url                  text NOT NULL,
  ficha_url            text,
  licencia             text,
  licencia_verificada  boolean NOT NULL DEFAULT false,
  fecha_dato           date,
  fecha_dato_origen    text,
  campos               jsonb NOT NULL DEFAULT '{}'::jsonb,
  advertencias         jsonb NOT NULL DEFAULT '[]'::jsonb,
  pendientes           jsonb NOT NULL DEFAULT '[]'::jsonb,
  version_publicada    bigint,
  sincronizada_en      timestamptz NOT NULL DEFAULT now()
);

-- Cada carga produce una versión inmutable
CREATE TABLE IF NOT EXISTS geo.version (
  version_id           bigserial PRIMARY KEY,
  fuente_id            text NOT NULL REFERENCES geo.fuente(fuente_id),
  estado               text NOT NULL CHECK (estado IN ('en_proceso','publicada','reemplazada','cuarentena')),
  sintetico            boolean NOT NULL DEFAULT false,
  inicio               timestamptz NOT NULL DEFAULT now(),
  fin                  timestamptz,
  registros_esperados  integer,
  registros_recibidos  integer,
  hash_contenido       text,
  bitacora             jsonb NOT NULL DEFAULT '[]'::jsonb,
  validaciones         jsonb NOT NULL DEFAULT '[]'::jsonb,
  motivo_cuarentena    text
);

DO $$ BEGIN
  ALTER TABLE geo.fuente
    ADD CONSTRAINT fuente_version_publicada_fk
    FOREIGN KEY (version_publicada) REFERENCES geo.version(version_id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Objetos geográficos o relacionales de cualquier fuente
CREATE TABLE IF NOT EXISTS geo.objeto (
  version_id   bigint NOT NULL REFERENCES geo.version(version_id),
  fuente_id    text   NOT NULL REFERENCES geo.fuente(fuente_id),
  llave        text   NOT NULL,
  nombre       text,
  padre        text,
  atributos    jsonb  NOT NULL DEFAULT '{}'::jsonb,
  geom         geometry(MultiPolygon, 4326)
);
CREATE INDEX IF NOT EXISTS objeto_version_llave_idx ON geo.objeto (version_id, llave);
CREATE INDEX IF NOT EXISTS objeto_padre_idx ON geo.objeto (version_id, padre);
CREATE INDEX IF NOT EXISTS objeto_geom_idx ON geo.objeto USING gist (geom);

-- Conteo de predios por lote y estrato (fuente 2026). No contiene identificadores prediales.
CREATE TABLE IF NOT EXISTS geo.conteo_lote_estrato (
  version_id   bigint   NOT NULL REFERENCES geo.version(version_id),
  lote         text     NOT NULL,
  estrato      smallint NOT NULL,
  n_predios    integer  NOT NULL CHECK (n_predios > 0)
);
CREATE INDEX IF NOT EXISTS conteo_lote_idx ON geo.conteo_lote_estrato (version_id, lote);

-- Solo lo publicado: cada fuente expone únicamente su versión publicada
CREATE OR REPLACE VIEW geo.v_objeto_publicado AS
SELECT o.*
FROM geo.objeto o
JOIN geo.fuente f ON f.fuente_id = o.fuente_id AND f.version_publicada = o.version_id;

CREATE OR REPLACE VIEW geo.v_conteo_publicado AS
SELECT c.*
FROM geo.conteo_lote_estrato c
JOIN geo.fuente f ON f.fuente_id = 'sdp_estrato_predio_2026' AND f.version_publicada = c.version_id;

-- Predios por estrato en cada manzana: unión oficial lote -> manzana (MANZCODIGO de la capa Lote).
-- No se infiere la manzana recortando el código del lote.
CREATE OR REPLACE VIEW geo.v_predios_manzana AS
SELECT l.padre AS manzana, c.estrato, sum(c.n_predios)::bigint AS n_predios
FROM geo.v_conteo_publicado c
JOIN geo.v_objeto_publicado l
  ON l.fuente_id = 'mr_lote_manzana' AND l.llave = c.lote
GROUP BY l.padre, c.estrato;

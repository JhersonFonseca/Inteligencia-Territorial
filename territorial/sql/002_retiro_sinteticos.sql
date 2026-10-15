-- Permite retirar de la consulta versiones sintéticas sin borrarlas (estado 'retirada').
-- Idempotente y sin borrados.
DO $$
DECLARE nombre text;
BEGIN
  SELECT conname INTO nombre FROM pg_constraint
   WHERE conrelid = 'geo.version'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%en_proceso%'
     AND pg_get_constraintdef(oid) NOT LIKE '%retirada%';
  IF nombre IS NOT NULL THEN
    EXECUTE format('ALTER TABLE geo.version DROP CONSTRAINT %I', nombre);
    ALTER TABLE geo.version ADD CONSTRAINT version_estado_check
      CHECK (estado IN ('en_proceso','publicada','reemplazada','cuarentena','retirada'));
  END IF;
END $$;

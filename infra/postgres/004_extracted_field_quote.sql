-- Add quote column to extracted_fields to persist verbatim source page text
ALTER TABLE extracted_fields ADD COLUMN IF NOT EXISTS quote TEXT;

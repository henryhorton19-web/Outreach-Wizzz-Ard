# Database and Storage Schema Documentation

### `custom_sourcing_prompts`

- `id` (text, primary key)
- `display_name` (text, non-null)
- `guidance` (text, non-null)
- `category` (text, default "general")
- `is_default` (boolean, default false)
- `created_at` (iso-8601 string)
- `updated_at` (iso-8601 string)

---

### `voices`

- `id` (text, primary key)
- `display_name` (text, non-null)
- `blocks` (array of Block objects)
- `style` (Style object)
- `evidence` (EvidencePref object)
- `created_at` (iso-8601 string)
- `updated_at` (iso-8601 string)

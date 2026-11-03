# Preset range selection

Preset management now supports selecting a continuous range from the last manually checked item to a visible message or exchange. Selection includes both endpoints, works across pagination, and preserves existing selections. Existing presets keep range selection within their current excerpt; history selection continues to use original message indexes.

The Preset dialogues entry has moved from below the persona capabilities to the editor header. A Back to top button appears after scrolling the preset management page. These controls change local selection and navigation only; the existing save and approval actions continue to commit content changes.

Persona presets now open the existing Review examples route with the selected persona and presets tab. The former `/persona/:personaId/presets` route redirects to `/persona-example-review?personaId=...&tab=presets` for saved links.

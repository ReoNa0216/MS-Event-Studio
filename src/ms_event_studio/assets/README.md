# Application icon provenance

The master icon was generated for MS Event Studio on 2026-08-12 with OpenAI's
built-in image generation tool. It is an original, text-free scientific mark:
a PC34-like chromatographic peak with a precise apex marker on a dark navy
rounded-square tile. The generated result already contained a native alpha
channel; no chroma-key removal or manual scientific-data transformation was
applied.

Generation prompt:

> Create a polished original app icon for a cross-platform scientific desktop
> application called MS Event Studio. Logo-brand icon only, no text or letters.
> Show one elegant mass-spectrometry/chromatography peak waveform rising from a
> thin baseline, with a precise circular apex marker and a subtle scan/ruler
> cue. Dark navy rounded-square tile; luminous cyan, teal, and mint accents;
> crisp geometric vector-like construction, strong silhouette at 16–32 px,
> centered with generous safe margins, professional analytical-instrument
> aesthetic, no DNA helix, no flask, no medical cross, no mockup, no shadow
> outside the tile. Keep the area outside the rounded tile transparent; if true
> transparency is unavailable, use solid magenta #FF00FF only as an outer
> chroma-key background and nowhere in the icon.

`app_icon_master.png` is the source asset. Run
`python -m desktop_bundle.generate_icons --runtime-assets` for committed Tk PNG
sizes. Native packaging invokes the same module to derive `.ico` or `.icns`
files in the ignored `build/icons/` directory.

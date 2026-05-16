# print-proxy-prep

CLI tool that downloads card images from Google Drive, crops bleed edges, and generates front/back PDFs for double-sided printing.

## Usage

Provide an XML order file describing which card images to include:

```bash
print-proxy-prep order.xml
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `-o, --output DIR` | `./output` | Base output directory |
| `--paper {letter,a4,legal}` | `letter` | Page size |
| `--orientation {portrait,landscape}` | `portrait` | Page orientation |
| `--dpi N` | `1200` | Max DPI before downscaling |
| `--vibrance` | off | Apply vibrance LUT |
| `--cardback PATH` | bundled `cardback.jpg` | Custom cardback image |
| `--cache-dir DIR` | `images/` next to script | Where to store downloaded/cropped images |
| `--clear-cache` | — | Delete cached images and exit |

### XML format

```xml
<order>
  <fronts>
    <card>
      <id>GOOGLE_DRIVE_FILE_ID</id>
      <slots>1,2,3</slots>
      <name>card_name.jpg</name>
    </card>
  </fronts>
  <backs>
    <card>
      <id>GOOGLE_DRIVE_FILE_ID</id>
      <slots>1,2,3</slots>
      <name>back_name.jpg</name>
    </card>
  </backs>
</order>
```

Each `<card>` entry specifies a Google Drive file ID, which slots it fills, and the filename (used for the file extension). Cards in `<backs>` override the default cardback for their slots.

## Nix

### Run directly

```bash
nix run github:your-user/print-proxy-prep -- order.xml
```

### Dev shell

```bash
nix develop
python main.py order.xml
```

### Use from another flake

The flake exports `lib.mkPrintProxyPrep` so a consuming service can bake in custom cache and output paths:

```nix
{
  inputs.print-proxy-prep.url = "github:your-user/print-proxy-prep";

  outputs = { self, nixpkgs, print-proxy-prep, ... }: {
    packages.x86_64-linux.default = print-proxy-prep.lib.mkPrintProxyPrep {
      pkgs = import nixpkgs { system = "x86_64-linux"; };
      cacheDir = "/var/lib/print-proxy-prep/cache";
      outputDir = "/var/lib/print-proxy-prep/output";
    };
  };
}
```

An overlay is also available:

```nix
{
  nixpkgs.overlays = [ print-proxy-prep.overlays.default ];
}
```

This adds `pkgs.print-proxy-prep` with default paths (controllable at runtime via CLI flags).

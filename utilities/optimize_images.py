#!/usr/bin/env python3
"""Resize oversized WebP images in the website, with external backups."""
import argparse
from pathlib import Path
import shutil
import tempfile
import os
import re
from urllib.parse import unquote, urlsplit
from PIL import Image

def discover_backgrounds(root):
    """Recognize background declarations, including shared CSS variables."""
    backgrounds = set()
    for css in sorted(root.rglob('*.css')):
        if '.git' in css.relative_to(root).parts or css.is_symlink():
            continue
        text = re.sub(r'/\*.*?\*/', '', css.read_text(encoding='utf-8'), flags=re.S)
        for declaration in re.findall(r'(?:--background-image|background-image|background)\s*:\s*([^;}]+)', text, re.I):
            for raw in re.findall(r'url\(\s*([^)]+?)\s*\)', declaration, re.I):
                url = urlsplit(raw.strip().strip('"\''))
                if url.scheme or url.netloc or not url.path:
                    continue
                name = unquote(url.path)
                # Custom properties can be resolved where the shared style uses them.
                bases = [css.parent, root / 'assets']
                candidates = [root / name.lstrip('/')] if name.startswith('/') else [base / name for base in bases]
                found = False
                for candidate in candidates:
                    candidate = candidate.resolve()
                    if candidate.is_relative_to(root) and candidate.is_file():
                        backgrounds.add(candidate.relative_to(root))
                        found = True
                if not found:
                    print(f'WARNING unresolved background in {css.relative_to(root)}: {name}')
    return backgrounds


def limits(rel, backgrounds):
    if rel.parent == Path('assets/thumbnails'):
        return 'homepage thumbnail', (240, 180)
    if 'thumbnails' in rel.parts[:-1]:
        return 'reference thumbnail', (200, 150)
    if rel in backgrounds or 'backgrounds' in rel.parts[:-1]:
        return 'background', (1920, 1080)
    return 'illustration', (1600, 1600)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', default=str(Path(__file__).resolve().parent.parent), help='website root (default: parent of utilities directory)')
    parser.add_argument('--dry-run', action='store_true', help='report only; write nothing')
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    if not (root / 'index.html').is_file() or not (root / 'assets').is_dir():
        parser.error('Root must contain index.html and assets/')
    backup = root.parent / (root.name + '-image-backup')
    if backup.exists() and (backup.is_symlink() or backup.resolve().is_relative_to(root)):
        parser.error('Backup directory must be outside the repository and not a symlink')
    backgrounds = discover_backgrounds(root)
    print(f'Discovered {len(backgrounds)} CSS background image(s).')
    changed = skipped = errors = saved = 0
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if path.suffix.lower() != '.webp' or '.git' in rel.parts or 'assets' not in rel.parts[:-1] or path.is_symlink() or not path.is_file():
            continue
        kind, (mw, mh) = limits(rel, backgrounds)
        tmp = None
        try:
            with Image.open(path) as im:
                w, h = im.size
                scale = min(1, mw / w, mh / h if mh else 1)
                if scale == 1 or getattr(im, 'is_animated', False):
                    print(f'SKIP {rel} ({w}x{h})' + (' [animated]' if getattr(im, 'is_animated', False) else ''))
                    skipped += 1
                    continue
                size = (max(1, int(w * scale)), max(1, int(h * scale)))
                old = path.stat().st_size
                print(f'{"PLAN" if args.dry_run else "RESIZE"} {rel} [{kind}] {w}x{h} -> {size[0]}x{size[1]}', end='')
                if args.dry_run:
                    print()
                    changed += 1
                    continue
                resized = im.resize(size, Image.Resampling.LANCZOS)
                options = {k: im.info[k] for k in ('icc_profile', 'exif', 'xmp') if im.info.get(k)}
                with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.webp', delete=False) as f:
                    tmp = Path(f.name)
                resized.save(tmp, format='WEBP', quality=85, method=6, **options)
            dest = backup / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copy2(path, dest)
            new = tmp.stat().st_size
            shutil.copymode(path, tmp)
            os.replace(tmp, path)
            tmp = None
            saved += old - new
            changed += 1
            print(f'  {old:,} -> {new:,} bytes')
        except Exception as exc:
            errors += 1
            print(f'\nERROR {rel}: {exc}')
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
    print(f'\n{"Would resize" if args.dry_run else "Resized"}: {changed}; skipped: {skipped}; errors: {errors}')
    if not args.dry_run:
        print(f'Net bytes saved: {saved:,}\nOriginals backed up to: {backup}')
    return bool(errors)

if __name__ == '__main__':
    raise SystemExit(main())

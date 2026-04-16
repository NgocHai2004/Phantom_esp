"""
_patch.py — Fix App._build_content: stack Encrypt (top) / Decrypt (bottom) vertically
"""

with open("en_de.py", "r", encoding="utf-8") as f:
    src = f.read()

old = '''    # ── CONTENT ───────────────────────────────────────────────────────────────
    def _build_content(self, cf):
        """Content area: Encrypt panel (left) | divider | Decrypt panel (right)."""
        cf.grid_rowconfigure(0, weight=1)
        cf.grid_columnconfigure(0, weight=1)
        cf.grid_columnconfigure(1, weight=0)   # divider
        cf.grid_columnconfigure(2, weight=1)

        # Encrypt content
        enc_cf = ctk.CTkFrame(cf, fg_color=C_BG, corner_radius=0)
        enc_cf.grid(row=0, column=0, sticky="nsew")
        self._enc_page._build_content(enc_cf)

        # Vertical divider
        ctk.CTkFrame(cf, fg_color=C_BORDER, width=1,
                     corner_radius=0).grid(row=0, column=1, sticky="ns")

        # Decrypt content
        dec_cf = ctk.CTkFrame(cf, fg_color=C_BG, corner_radius=0)
        dec_cf.grid(row=0, column=2, sticky="nsew")
        self._dec_page._build_content(dec_cf)'''

new = '''    # ── CONTENT ───────────────────────────────────────────────────────────────
    def _build_content(self, cf):
        """Content area: Encrypt panel (top) ── divider ── Decrypt panel (bottom)."""
        cf.grid_rowconfigure(0, weight=1)   # encrypt
        cf.grid_rowconfigure(1, weight=0)   # divider
        cf.grid_rowconfigure(2, weight=1)   # decrypt
        cf.grid_columnconfigure(0, weight=1)

        # Encrypt content (top)
        enc_cf = ctk.CTkFrame(cf, fg_color=C_BG, corner_radius=0)
        enc_cf.grid(row=0, column=0, sticky="nsew")
        self._enc_page._build_content(enc_cf)

        # Horizontal divider
        ctk.CTkFrame(cf, fg_color=C_BORDER, height=1,
                     corner_radius=0).grid(row=1, column=0, sticky="ew")

        # Decrypt content (bottom)
        dec_cf = ctk.CTkFrame(cf, fg_color=C_BG, corner_radius=0)
        dec_cf.grid(row=2, column=0, sticky="nsew")
        self._dec_page._build_content(dec_cf)'''

assert old in src, "Could not find old _build_content block"
src = src.replace(old, new, 1)

# Also restore window size to original (no need for extra width)
src = src.replace('self.geometry("1440x820")', 'self.geometry("1200x900")')
src = src.replace('self.minsize(1100, 680)', 'self.minsize(960, 720)')

with open("en_de.py", "w", encoding="utf-8") as f:
    f.write(src)

print("Done. Lines:", src.count('\n'))

TweetNaCl 20140427 (Bernstein, Lange, Schwabe et al.), public domain, https://tweetnacl.cr.yp.to/.
Vendored unmodified. STOZHER uses only `crypto_sign_open` (Ed25519 verification of acknowledgements,
docs/control.md). `randombytes` is provided by `stz_ack.cpp` as a trap: nothing here may generate keys.
TweetNaCl left-shifts negative integers in its field arithmetic (well known, harmless on two's-complement
targets). The host test build therefore disables only UBSan's `shift-base` check for this library
(`library.json`); everything else stays sanitized.

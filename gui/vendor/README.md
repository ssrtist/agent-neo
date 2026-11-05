# gui/vendor: third-party browser libraries

Vendored once from upstream npm release tarballs. The GUI makes **no runtime network fetches**: these files are loaded
from disk by the pytauri window. Every file below is byte-identical to the file of the same path in the package tarball
(checked 2026-10-30 for agent365 0.4.0).

| Library | Version | Licence | Tarball | Tarball SHA-256 |
|---|---|---|---|---|
| marked | 18.1.0 | MIT (`marked/LICENSE`) | `marked/-/marked-18.1.0.tgz` | `39a404686c55fc2d862216e0cbb70f6bedc456adaeb5fe57b642036455f9ffc0` |
| DOMPurify | 3.4.16 | Apache-2.0 OR MPL-2.0 (`dompurify/LICENSE`, `dompurify/LICENSE-MPL`) | `dompurify/-/dompurify-3.4.16.tgz` | `d288ced25e6bda3e5fc76d1fc9bca899260cf886e005e84f232e686ada57f1b4` |
| highlight.js (`@highlightjs/cdn-assets`) | 11.12.0 | BSD-3-Clause (`highlight/LICENSE`) | `@highlightjs/cdn-assets/-/cdn-assets-11.12.0.tgz` | `b8a006d30f45afe783072569f3d69c5b60c0e7b9ca28cd474e12f2584e2a3bd9` |

## Files

| File here | Path in the tarball | Bytes | SHA-256 |
|---|---|---|---|
| `marked/marked.esm.js` | `package/lib/marked.esm.js` | 46345 | `054d73b676031dc8e60e247ef228044fbeebe10af14bac1a6e53852f80f826e5` |
| `marked/LICENSE` | `package/LICENSE` | 2942 | `8e3a3f82f59a60958f56ca08f445647c32a4733dc7ca6c2c46f6eb898471ab9c` |
| `dompurify/purify.es.mjs` | `package/dist/purify.es.mjs` | 84443 | `c44274a7959cfdd4da871fa78a5d5fbbef55db68d118c5c0833bc4b5cf9633ad` |
| `dompurify/LICENSE` | `package/LICENSE` | 11358 | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| `dompurify/LICENSE-MPL` | `package/LICENSE-MPL` | 16726 | `fab3dd6bdab226f1c08630b1dd917e11fcb4ec5e1e020e2c16f83a0a13863e85` |
| `highlight/core.min.js` | `package/es/core.min.js` | 20501 | `67a339aa68880a40c9803def9c8cebb2225dec76230382db86e349370dee70e9` |
| `highlight/LICENSE` | `package/LICENSE` | 1514 | `6c081431591d9df696c82dc598fe1423765b8a299b200ed00b281afd0f64c490` |
| `highlight/languages/bash.min.js` | `package/es/languages/bash.min.js` | 3182 | `7db1758fe6f04cd410760c29879812a3774edc54edcc91323511a24efc0b9e61` |
| `highlight/languages/csharp.min.js` | `package/es/languages/csharp.min.js` | 4159 | `74bb1a32b37d581173fc9d880ab5be6c7e448bbb12552c3e2a3ac7fc3c57cf8f` |
| `highlight/languages/css.min.js` | `package/es/languages/css.min.js` | 13544 | `2f4264d9a07a4f5446e51e2d93b0c0e031b4fd910e8e44984cd17f996a990b99` |
| `highlight/languages/diff.min.js` | `package/es/languages/diff.min.js` | 688 | `cbd7cf3b49bee36d6a0cf97789a7c5e68fbfd4231e86a81966f9b78c7bc127d0` |
| `highlight/languages/dos.min.js` | `package/es/languages/dos.min.js` | 1430 | `ab71cfa8dc6c02310b4e5eec03a94666da4be476187d53f385a934bd4fb760d2` |
| `highlight/languages/ini.min.js` | `package/es/languages/ini.min.js` | 1042 | `b2330623daa8da0c4ef98891b97f393daa2e809721842c312f217e0339e255db` |
| `highlight/languages/javascript.min.js` | `package/es/languages/javascript.min.js` | 6599 | `c3dbd515a79e2c00ff4535aa5ad2fe539fb2587d047f9ab826107d0784da7bb6` |
| `highlight/languages/json.min.js` | `package/es/languages/json.min.js` | 689 | `8f2343ffbcc4fdea4b336a4241d54d223d52f99b67729d2d6d3556bbf66908ae` |
| `highlight/languages/markdown.min.js` | `package/es/languages/markdown.min.js` | 2206 | `73cb474b4687421a0949ded1ba8314ec74c26058688dc7f6154084240352cc79` |
| `highlight/languages/powershell.min.js` | `package/es/languages/powershell.min.js` | 4438 | `67c95a71dae92f010a15e959158aaf682cd850a008d466f1c5d40394cbe22f12` |
| `highlight/languages/python.min.js` | `package/es/languages/python.min.js` | 3655 | `fd5d479dcc9d07b957cf07b1df76d3d8fb6ee6f6d1437238d9530a9be724c046` |
| `highlight/languages/sql.min.js` | `package/es/languages/sql.min.js` | 6545 | `79bead5cf2bfa93d3982073d000a97802f394fe287736644bab30f40cb5c7380` |
| `highlight/languages/typescript.min.js` | `package/es/languages/typescript.min.js` | 7868 | `0e10ce9a81d42f8d43a54007ca7e31fe22ad801b2a2e1693d31736aa79fa5404` |
| `highlight/languages/xml.min.js` | `package/es/languages/xml.min.js` | 1992 | `2e8c8ba1e7a200cd83dfbf9c746b8e00f7d7bad966dbfd83eca142cf2caa4dd7` |
| `highlight/languages/yaml.min.js` | `package/es/languages/yaml.min.js` | 1947 | `7010955f1f87aef1de085483d79d42817544aaf03c4f57a6fd4eb838f2cdf609` |

## Updating

Download the new release tarball from npm, copy the same paths, update this table (versions, sizes,
SHA-256) and run the GUI e2e (`tests\e2e\gui_e2e.py`): it checks Markdown tables, syntax colouring and sanitising.

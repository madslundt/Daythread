# Interface localization

Daythread currently ships English (`en`) and Danish (`da`) interface text in the web app and iPhone app. **Interface language** is a device preference under Settings → Appearance. **System language** follows the browser or iPhone language; a chosen language overrides it on that device. This preference is independent of transcription routes, transcript translation targets, and the language of generated recaps. Recordings, names, transcripts, and model responses stay in their original language.

## Contribute a translation

1. Add the language code to `interfaceLanguages` in `src/web/src/i18n.ts`, import a matching JSON catalog, and add it to `catalogs`. Keep the English text in `src/web/src/locales/en.json` as the source key. Copy every key into `src/web/src/locales/<code>.json` and translate its value. Keep brand names, API names, URLs, and technical identifiers intact. The English source text is the runtime fallback when a translation is missing.
2. Add `src/ios/DaythreadApp/<code>.lproj/Localizable.strings` and `InfoPlist.strings` with the same keys as the English files. Translate the values, preserving format placeholders and punctuation needed by SwiftUI. The latter file covers system permission prompts. Add the language to the Interface language picker in `SettingsView.swift`. Run `xcodegen generate --spec src/ios/project.yml --project src/ios` so the new resources appear in the checked-in Xcode project.
3. Run `python3 scripts/check_locales.py`, `npm --prefix src/web test`, and `npm --prefix src/web run build`. For iPhone changes, also run the simulator build in [operations](README.md#iphone-companion). Check the main screens at a narrow width and on an iPhone simulator, including long labels, buttons, alerts, accessibility labels, dates, and plural wording.

## Add or change interface text

For web copy, write an English source phrase in a component as `t("Save preferences")`, then add the identical key to `en.json` and a translation to every other JSON catalog. Use `t()` only for interface text: never wrap route names, HTML roles, CSS classes, IDs, API values, or user and model content. For text with changing values, use `t("{count} recordings", { count })` and preserve the placeholder name in every translation. Translate complete sentences rather than joining fragments. Date display helpers in `src/web/src/format.ts` and `calendarDates.ts` use the selected interface locale; date arithmetic and the backend timezone remain separate.

For SwiftUI, literals such as `Text("Settings")`, `Button("Save")`, and `Label("Today", systemImage: "sun.max")` use the matching `Localizable.strings` key. Add the English literal and its translations to every `.strings` file. Dynamic SwiftUI titles need a `LocalizedStringKey` for interface copy; leave user data as `String`. Keep interpolation tokens and format specifiers consistent across languages.

The check script verifies catalog parity, nonempty translations, and web `t("…")` calls. It cannot judge translation quality. Review Danish copy in context before merging; use clear, natural wording and keep the meaning of privacy and data provenance notices exact.

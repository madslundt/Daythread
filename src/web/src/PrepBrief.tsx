import { t } from "./i18n";
import { useState } from "react";
import { dateTimeLabel } from "./format";
import { prepSections, type PrepResult, type PrepSource } from "./prep";
import { SourceList } from "./SourceList";

export function PrepBrief({
  result,
  timezone,
}: {
  result: PrepResult;
  timezone: string;
}) {
  const [viewSource, setViewSource] = useState<PrepSource>();
  return (
    <div aria-live="polite">
      <p className="caption">
        {t("Generated")}{" "}{dateTimeLabel(result.generated_at, timezone)} ·{" "}
        {result.model}{t(". Evidence checked")}{" "}{dateTimeLabel(result.as_of, timezone)}
        {t(". Interpretation of selected excerpts; review citations.")}{" "}</p>
      {prepSections.map(([key, title]) => (
        <section key={key}>
          <h3>{title}</h3>
          {result[key].length ? (
            result[key].map((item, index) => (
              <div className="prep-item" key={index}>
                <p>{item.text}</p>
                {item.citations.map((citation, i) => (
                  <button
                    key={i}
                    className="prep-citation text-button"
                    onClick={() =>
                      setViewSource(
                        result.sources.find(
                          (source) => source.id === citation.source_id,
                        ),
                      )
                    }
                  >
                    {dateTimeLabel(citation.started_at, timezone)} · “
                    {citation.excerpt}”
                  </button>
                ))}
              </div>
            ))
          ) : (
            <p className="caption">
              {t("No supported items in the reviewed excerpts.")}{" "}</p>
          )}
        </section>
      ))}
      <details>
        <summary>{t("Evidence coverage and limits")}</summary>
        {result.coverage.limitations.map((text) => (
          <p key={text}>{text}</p>
        ))}
        <p>
          {result.coverage.omitted_matches} {t("matching recordings were not selected.")}{" "}</p>
        {result.reviewed_excerpts.map((excerpt) => (
          <details key={excerpt.id}>
            <summary>
              {dateTimeLabel(excerpt.started_at, timezone)} {t("· reviewed excerpt ·")}{" "}
              {excerpt.omitted_characters} {t("characters omitted")}{" "}</summary>
            <p className="transcript-text">{excerpt.text}</p>
          </details>
        ))}
      </details>
      {viewSource ? (
        <section>
          <h3>{t("Cited recording")}</h3>
          <SourceList
            key={viewSource.version}
            sources={[viewSource]}
            idPrefix="prep-cited"
            timezone={timezone}
            showDate
            expanded
          />
        </section>
      ) : null}
    </div>
  );
}

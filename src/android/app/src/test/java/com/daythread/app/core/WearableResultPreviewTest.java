package com.daythread.app.core;

import org.junit.Test;
import static org.junit.Assert.*;

public class WearableResultPreviewTest {
    @Test public void countIsPreservedWhileOnlyTheBackendLatestQuestionAndAnswerAreDisplayed() {
        String latest = "When did World War II begin?\nWorld War II began on 1 September 1939.";
        Json result = new Json().put("question_count", 3).put("text", latest)
            .put("questions", new Json.JsonArray(java.util.List.of("Older question?", "Another question?", "Latest question?")));
        WearableResultPreview preview = WearableResultPreview.from("answer", result, "Ready");
        assertEquals(Integer.valueOf(3), preview.questionCount());
        assertEquals(latest, preview.text());
        assertFalse(preview.text().contains("Older question"));
    }
    @Test public void zeroOneAndMultipleCountsRemainAvailableForLocalizedPluralTitles() {
        for (int count : new int[]{0, 1, 2, 12, 1000}) {
            WearableResultPreview preview = WearableResultPreview.from("answer", new Json().put("question_count", count).put("text", "Result"), "Ready");
            assertEquals(Integer.valueOf(count), preview.questionCount());
        }
    }
    @Test public void legacyMalformedAndNonAnswerResultsRetainTheirOriginalFallback() {
        for (String value : new String[]{"null", "-1", "1.5", "true", "\"3\"", "1001", "2147483648"}) {
            Json result = new Json("{\"question_count\":" + value + ",\"text\":\"Older result\"}");
            WearableResultPreview preview = WearableResultPreview.from("answer", result, "Ready");
            assertNull(preview.questionCount()); assertEquals("Older result", preview.text());
        }
        assertNull(WearableResultPreview.from("summarize", new Json().put("question_count", 3), "Ready").questionCount());
        assertEquals("Saved summary", WearableResultPreview.from("summarize", new Json().put("summary", "Saved summary"), "Ready").text());
        assertEquals("Ready", WearableResultPreview.from("answer", new Json(), "Ready").text());
    }
    @Test public void longCountValuesHonorTheSameServerBounds() {
        assertEquals(Integer.valueOf(1000), WearableResultPreview.from("answer", new Json().put("question_count", 1000L), "Ready").questionCount());
        assertNull(WearableResultPreview.from("answer", new Json().put("question_count", 1001L), "Ready").questionCount());
    }
    @Test public void answerLineBreaksAndMathOperatorsArePreserved() {
        String answer = "What is 2 * 3?\n2 * 3 = 6.";
        assertEquals(answer, WearableResultPreview.from("answer", new Json().put("text", answer).put("question_count", 1), "Ready").text());
    }
    @Test public void englishAndDanishTitlesIncludeCountAndHandleNoQuestionAndSingular() throws Exception {
        String[][] expected = {{"values", "0 questions · No question found", "1 question · Latest answer", "3 questions · Latest answer"},
            {"values-da", "0 spørgsmål · Intet spørgsmål fundet", "1 spørgsmål · Seneste svar", "3 spørgsmål · Seneste svar"}};
        for (String[] locale : expected) {
            var document = javax.xml.parsers.DocumentBuilderFactory.newInstance().newDocumentBuilder()
                .parse(new java.io.File("src/main/res/" + locale[0] + "/strings.xml"));
            var xpath = javax.xml.xpath.XPathFactory.newInstance().newXPath();
            String zero = xpath.evaluate("/resources/string[@name='no_recent_question_count']", document);
            String one = xpath.evaluate("/resources/plurals[@name='latest_answer_question_count']/item[@quantity='one']", document);
            String many = xpath.evaluate("/resources/plurals[@name='latest_answer_question_count']/item[@quantity='other']", document);
            assertEquals(locale[1], String.format(java.util.Locale.ROOT, zero, 0));
            assertEquals(locale[2], String.format(java.util.Locale.ROOT, one, 1));
            assertEquals(locale[3], String.format(java.util.Locale.ROOT, many, 3));
        }
    }
}

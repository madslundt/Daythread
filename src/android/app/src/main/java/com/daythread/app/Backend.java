package com.daythread.app;

import com.daythread.app.core.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;

final class Backend {
    record Response(Json json, String profile) {}
    private static final class Rejected extends IOException {
        final boolean fallback;
        Rejected(int status) { super("Backend returned HTTP " + status + ". Recordings remain on this phone."); fallback = status == 502 || status == 503 || status == 504; }
    }
    static Response request(Destination destination, String path, String method, File audio, Json body) throws IOException {
        IOException failure = new IOException("Backend unavailable");
        for (String endpoint : destination.endpoints()) {
            try { return request(endpoint, destination.profile(), path, method, audio, body); }
            catch (Rejected e) { if (!e.fallback) throw e; failure = e; }
            catch (IOException e) { failure = e; }
        }
        throw failure;
    }
    private static Response request(String endpoint, String profile, String path, String method, File audio, Json body) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(endpoint + path).openConnection();
        try {
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(10000); connection.setReadTimeout(30000);
            connection.setRequestMethod(method); connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("X-Omi-Profile", profile);
            connection.setRequestProperty("Cache-Control", "no-store");
            if (audio != null || body != null) {
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", audio == null ? "application/json" : "audio/wav");
                byte[] bytes = body == null ? null : body.toString().getBytes(StandardCharsets.UTF_8);
                connection.setFixedLengthStreamingMode(audio == null ? bytes.length : audio.length());
                try (OutputStream output = connection.getOutputStream()) {
                    if (audio == null) output.write(bytes);
                    else try (InputStream input = new FileInputStream(audio)) { Streams.copy(input, output); }
                }
            }
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) throw new Rejected(status);
            try (InputStream input = connection.getInputStream()) {
                byte[] bytes = Streams.read(input, 1_000_001);
                if (bytes.length > 1_000_000) throw new Rejected(502);
                try { return new Response(new Json(new String(bytes, StandardCharsets.UTF_8)), connection.getHeaderField("X-Omi-Profile")); }
                catch (RuntimeException e) { throw new Rejected(502); }
            }
        } finally { connection.disconnect(); }
    }
    static void upload(Outbox outbox, Outbox.Item item) throws IOException {
        String path = switch (item.kind()) {
            case "audio" -> "/v1/audio/" + item.id() + "?device_id=" + encode(item.data().getString("device_id"))
                + "&started_at=" + encode(item.data().getString("started_at"));
            case "event" -> "/v1/events?receipt_only=true";
            case "gap" -> "/v1/gaps";
            case "generation" -> "/v1/jobs";
            default -> "/v1/capture-state/" + item.id();
        };
        Response response = request(item.destination(), path, item.kind().equals("event") || item.kind().equals("gap") || item.kind().equals("generation") ? "POST" : "PUT",
            item.kind().equals("audio") ? item.file() : null, item.kind().equals("audio") ? null : item.data());
        outbox.acknowledge(item, response.json, response.profile);
    }
    private static String encode(String value) { try { return URLEncoder.encode(value, "UTF-8"); } catch (UnsupportedEncodingException e) { throw new AssertionError(e); } }
}

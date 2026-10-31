package com.daythread.app;

import com.daythread.app.core.*;
import org.junit.*;
import org.junit.rules.TemporaryFolder;
import static org.junit.Assert.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.Function;

public class BackendTest {
    @Rule public TemporaryFolder temporary = new TemporaryFolder();
    record Request(String line, Map<String, String> headers, byte[] body) {}
    record Reply(int status, Json body, String profile) {}
    static final class Server implements AutoCloseable {
        final ServerSocket socket = new ServerSocket(0, 10, InetAddress.getByName("127.0.0.1"));
        final BlockingQueue<Request> requests = new LinkedBlockingQueue<>();
        final Thread thread;
        Server(Function<Request, Reply> handler) throws IOException {
            thread = new Thread(() -> {
                try {
                    while (!socket.isClosed()) try (Socket client = socket.accept()) {
                        InputStream input = client.getInputStream(); String first = line(input);
                        Map<String, String> headers = new HashMap<>();
                        for (String header; !(header = line(input)).isEmpty();) {
                            int colon = header.indexOf(':'); headers.put(header.substring(0, colon).toLowerCase(Locale.ROOT), header.substring(colon + 1).trim());
                        }
                        Request request = new Request(first, headers, Streams.read(input, Integer.parseInt(headers.getOrDefault("content-length", "0"))));
                        requests.add(request); Reply reply = handler.apply(request);
                        byte[] bytes = reply.body.toString().getBytes(StandardCharsets.UTF_8);
                        OutputStream output = client.getOutputStream();
                        output.write(("HTTP/1.1 " + reply.status + " Test\r\nContent-Type: application/json\r\nX-Omi-Profile: " + reply.profile
                            + "\r\nContent-Length: " + bytes.length + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
                        output.write(bytes); output.flush();
                    }
                } catch (IOException ignored) {}
            }); thread.setDaemon(true); thread.start();
        }
        private static String line(InputStream input) throws IOException {
            ByteArrayOutputStream line = new ByteArrayOutputStream();
            for (int value; (value = input.read()) != -1 && value != '\n';) if (value != '\r') line.write(value);
            return line.toString("US-ASCII");
        }
        String origin() { return "http://127.0.0.1:" + socket.getLocalPort(); }
        @Override public void close() throws IOException { socket.close(); }
    }
    @Test public void gatewayFallbackPreservesProfileMethodAndPayload() throws Exception {
        try (Server unavailable = new Server(r -> new Reply(503, new Json(), "default"));
             Server available = new Server(r -> new Reply(200, new Json().put("ok", true), "my-profile"))) {
            Destination destination = new Destination(List.of(unavailable.origin(), available.origin()), "my-profile");
            Json body = new Json().put("event_id", UUID.randomUUID().toString());
            Backend.request(destination, "/v1/events?receipt_only=true", "POST", null, body);
            Request first = unavailable.requests.poll(1, TimeUnit.SECONDS), next = available.requests.poll(1, TimeUnit.SECONDS);
            assertEquals(first.line, next.line); assertArrayEquals(first.body, next.body);
            assertEquals("my-profile", next.headers.get("x-omi-profile"));
        }
    }
    @Test public void validationFailureAndRedirectNeverSendToFallback() throws Exception {
        for (int status : new int[]{401, 409, 422, 302}) {
            try (Server rejected = new Server(r -> new Reply(status, new Json(), "default"));
                 Server other = new Server(r -> new Reply(200, new Json(), "default"))) {
                assertThrows(IOException.class, () -> Backend.request(new Destination(List.of(rejected.origin(), other.origin()), "default"), "/v1/profiles", "GET", null, null));
                assertTrue(other.requests.isEmpty());
            }
        }
    }
    @Test public void wrongSuccessRetainsAudioAndRetrySendsIdenticalIdClockAndBytes() throws Exception {
        try (Server server = new Server(r -> new Reply(200, new Json().put("ok", true), "default"))) {
            Outbox outbox = new Outbox(temporary.newFolder());
            try (Outbox.Writer writer = outbox.begin(new Destination(List.of(server.origin()), "default"), "android-test", Instant.parse("2026-10-17T10:00:00Z"))) {
                writer.append(new byte[640], 0, 640, null);
            }
            Outbox.Item item = outbox.pending().get(0);
            assertThrows(IOException.class, () -> Backend.upload(outbox, item));
            assertThrows(IOException.class, () -> Backend.upload(outbox, outbox.pending().get(0)));
            Request first = server.requests.poll(1, TimeUnit.SECONDS), retry = server.requests.poll(1, TimeUnit.SECONDS);
            assertEquals(first.line, retry.line); assertTrue(first.line.contains(item.id())); assertTrue(first.line.contains("started_at=2026-10-17T10%3A00%3A00Z"));
            assertArrayEquals(first.body, retry.body); assertEquals(684, first.body.length); assertTrue(item.file().exists());
        }
    }
    @Test public void wearableRequestsPostStableJobsAndRequireMatchingReceipt() throws Exception {
        try (Server server = new Server(r -> new Reply(200, new Json(new String(r.body, StandardCharsets.UTF_8)).put("state", "queued"), "default"))) {
            Outbox outbox = new Outbox(temporary.newFolder());
            String id = outbox.enqueue("generation", new Destination(List.of(server.origin()), "default"),
                WearableAction.TRANSLATE.request(30, Instant.parse("2026-10-31T10:00:00Z"), "omi-test"));
            Backend.upload(outbox, outbox.pending().get(0));
            Request request = server.requests.poll(1, TimeUnit.SECONDS);
            assertTrue(request.line.startsWith("POST /v1/jobs "));
            Json body = new Json(new String(request.body, StandardCharsets.UTF_8));
            assertEquals(id, body.getString("id"));
            assertEquals("translate", body.getJson("parameters").getString("action"));
            assertTrue(outbox.pending().isEmpty());
        }
    }
}

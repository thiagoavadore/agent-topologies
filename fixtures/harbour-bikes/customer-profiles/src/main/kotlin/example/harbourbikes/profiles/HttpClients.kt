package example.harbourbikes.profiles

import java.net.http.HttpClient
import java.time.Duration

// Every outbound call goes through this client: 2 s to connect, 5 s per request.
object HttpClients {
    val shared: HttpClient = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(2))
        .build()
    val requestTimeout: Duration = Duration.ofSeconds(5)
}

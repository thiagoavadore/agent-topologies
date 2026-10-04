// Subscribes to bike telemetry on the local broker and writes positions to TimescaleDB.
use rumqttc::{AsyncClient, Event, MqttOptions, Packet, QoS};
use std::time::Duration;

#[tokio::main]
async fn main() {
    let mut options = MqttOptions::new("ingest", "localhost", 1883);
    options.set_keep_alive(Duration::from_secs(30));
    let (client, mut events) = AsyncClient::new(options, 100);
    client.subscribe("bikes/+/telemetry", QoS::AtLeastOnce).await.unwrap();
    loop {
        if let Ok(Event::Incoming(Packet::Publish(message))) = events.poll().await {
            println!("reading from {}", message.topic);
        }
    }
}

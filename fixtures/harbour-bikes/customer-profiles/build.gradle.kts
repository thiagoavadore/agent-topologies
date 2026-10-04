plugins {
    kotlin("jvm") version "2.0.21"
    id("org.springframework.boot") version "3.3.5"
}

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-web:3.3.5")
    implementation("org.postgresql:postgresql:42.7.4")
}

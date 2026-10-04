// payments-gateway wraps the card processor for charges and refunds.
package main

import (
	"log"
	"os"

	"gopkg.in/yaml.v3"
)

type Config struct {
	Processor struct {
		BaseURL    string `yaml:"base_url"`
		MerchantID string `yaml:"merchant_id"`
		APIKey     string `yaml:"api_key"`
		TimeoutMS  int    `yaml:"timeout_ms"`
		Retries    int    `yaml:"retries"`
	} `yaml:"processor"`
}

func loadConfig(path string) Config {
	raw, err := os.ReadFile(path)
	if err != nil {
		log.Fatal(err)
	}
	var cfg Config
	if err := yaml.Unmarshal([]byte(os.ExpandEnv(string(raw))), &cfg); err != nil {
		log.Fatal(err)
	}
	return cfg
}

func main() {
	cfg := loadConfig("config.yaml")
	log.Printf("payments-gateway up, processor %s", cfg.Processor.BaseURL)
}

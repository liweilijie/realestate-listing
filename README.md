# Distributed Data Collection System

A scalable and robust distributed system designed for high-concurrency data acquisition, featuring message queuing, automation, and persistence.

## 🚀 Features

1. Distributed Message Notification via Redis

   Utilizes Redis Pub/Sub to implement a lightweight and efficient message notification mechanism, enabling real-time communication between distributed components.

2. Multi-Process High-Concurrency Management
 
   Employs Python's multiprocessing module to manage multiple processes, ensuring high concurrency and optimal resource utilization across the system.

3. Automated Data Interaction with Selenium + ADS

   Integrates Selenium with the ADS (Automated Data Scraper) framework to automate complex data interactions, including handling dynamic content and JavaScript-rendered pages.

4. Flexible External Parameter Passing
 
   Supports the injection of multiple external parameters, allowing for customizable data collection tasks tailored to specific requirements.

5. Data Persistence to MySQL
 
   Collected data is stored in a MySQL database, ensuring reliable and structured data persistence for further analysis and processing.

6. Duplicate Filtering Mechanism
 
   Implements a robust deduplication process to filter out redundant data entries, maintaining the integrity and quality of the collected dataset.

## 📦 Tech Stack

- Python – Core programming language for system development.

- Redis – In-memory data structure store used for message queuing.

- Selenium – Tool for automating web browser interactions.

- ADS (Automated Data Scraper) – Framework for complex data scraping tasks.

- MySQL – Relational database for data storage.

- Multiprocessing Module – Python module for parallel execution of processes.
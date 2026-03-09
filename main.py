import urllib.request
import urllib.error
import json
import time
import os

# ==========================================
# 1. CONFIGURATION: Environment Variable
# ==========================================
# Securely fetch the API token from the DigitalOcean Functions environment
DO_API_TOKEN = os.getenv("DO_API_TOKEN")

if not DO_API_TOKEN:
    print("WARNING: DO_API_TOKEN environment variable is missing or empty!")
# ==========================================

def call_do_api(endpoint):
    """Helper function to make API calls using built-in urllib."""
    if endpoint.startswith("http"):
        url = endpoint
    else:
        url = f"https://api.digitalocean.com/v2/{endpoint}"
        
    req = urllib.request.Request(url)
    # Safely handle the case where the token is None
    token = DO_API_TOKEN if DO_API_TOKEN else ""
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError:
        pass 
    except Exception as e:
        print(f"API Error on {endpoint}: {e}")
    return {}

def fetch_latest_system_metric(metric_name, host_id, start, end):
    """Helper to fetch a specific DO metric and return the latest recorded value."""
    endpoint = f"monitoring/metrics/droplet/{metric_name}?host_id={host_id}&start={start}&end={end}"
    data = call_do_api(endpoint)
    try:
        results = data.get("data", {}).get("result", [])
        if results and len(results[0].get("values", [])) > 0:
            return float(results[0]["values"][-1][1])
    except Exception:
        pass
    return 0.0

def fetch_dcgm_metrics(ip_address):
    """Attempts to scrape the raw Prometheus text from the DCGM exporter on port 9400."""
    url = f"http://{ip_address}:9400/metrics"
    metrics = {}
    try:
        # Strict 2-second timeout so the script doesn't hang if the port is blocked
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2) as response:
            lines = response.read().decode('utf-8').splitlines()
            
            for line in lines:
                if line.startswith("#"): 
                    continue
                
                parts = line.split()
                if not parts:
                    continue
                
                val = float(parts[-1])
                
                # Extract the exact Prometheus keys from your output
                if line.startswith("DCGM_FI_DEV_GPU_TEMP"):
                    metrics["gpu_temp"] = val
                elif line.startswith("DCGM_FI_DEV_POWER_USAGE"):
                    metrics["power_usage"] = val
                elif line.startswith("DCGM_FI_DEV_GPU_UTIL"):
                    metrics["gpu_util"] = val
                elif line.startswith("DCGM_FI_DEV_FB_USED"):
                    metrics["vram_used_mb"] = val
                elif line.startswith("DCGM_FI_DEV_FB_FREE"):
                    metrics["vram_free_mb"] = val
                    
    except Exception:
        # Fails silently if port 9400 is closed, firewall blocks it, or server is off
        pass 
        
    return metrics

def main(args):
    """Main entry point for DigitalOcean Serverless Functions."""
    
    end_time = int(time.time())
    start_time = end_time - 300 # 5-minute rolling window
    
    # --- PAGINATION LOGIC ---
    droplets = []
    endpoint = "droplets?per_page=200"
    
    while endpoint:
        data = call_do_api(endpoint)
        droplets.extend(data.get("droplets", []))
        
        next_url = data.get("links", {}).get("pages", {}).get("next")
        if next_url:
            endpoint = next_url
        else:
            endpoint = None
    # ------------------------

    gpu_inventory = []
    category_counts = {
        "Idle": 0,
        "Over provisioned": 0,
        "Optimized": 0,
        "Under provisioned": 0,
        "Unknown (Off or No Agent)": 0
    }

    for d in droplets:
        size_slug = d.get("size_slug", "")
        if not size_slug:
            continue
            
        size_slug = size_slug.lower()
        
        # ONLY process high-compute droplets
        if "gpu" in size_slug or "so-16vcpu-128gb-intel" in size_slug or "so-32vcpu-256gb-intel" in size_slug:
            d_id = d.get("id")
            name = d.get("name")
            hourly_price = d.get("size", {}).get("price_hourly", 0.0)
            
            # 1. Extract the IP Address (Prefer Public, fallback to Private)
            networks = d.get("networks", {}).get("v4", [])
            ip_address = None
            for net in networks:
                if net.get("type") == "public":
                    ip_address = net.get("ip_address")
                    break
            if not ip_address and networks:
                ip_address = networks[0].get("ip_address")

            # 2. Try fetching Hardware GPU Metrics (DCGM)
            dcgm_data = fetch_dcgm_metrics(ip_address) if ip_address else {}
            dcgm_available = bool(dcgm_data)

            # 3. Fetch System Metrics (Load, Memory, CPU) via DO API
            load_15 = round(fetch_latest_system_metric("load_15", d_id, start_time, end_time), 2)
            bytes_to_gb = 1024 ** 3
            mem_total = round(fetch_latest_system_metric("memory_total", d_id, start_time, end_time) / bytes_to_gb, 2)
            mem_avail = round(fetch_latest_system_metric("memory_available", d_id, start_time, end_time) / bytes_to_gb, 2)
            mem_util_percent = 0.0
            if mem_total > 0:
                mem_util_percent = ((mem_total - mem_avail) / mem_total) * 100
            
            cpu_avg = 0.0
            cpu_endpoint = f"monitoring/metrics/droplet/cpu?host_id={d_id}&start={start_time}&end={end_time}"
            cpu_data = call_do_api(cpu_endpoint)
            try:
                results = cpu_data.get("data", {}).get("result", [])
                total_start, total_end, idle_start, idle_end = 0.0, 0.0, 0.0, 0.0
                if results:
                    for metric in results:
                        mode = metric.get("metric", {}).get("mode")
                        values = metric.get("values", [])
                        if len(values) >= 2:
                            val_start = float(values[0][1])
                            val_end = float(values[-1][1])
                            total_start += val_start
                            total_end += val_end
                            if mode == "idle":
                                idle_start = val_start
                                idle_end = val_end
                    total_diff = total_end - total_start
                    idle_diff = idle_end - idle_start
                    if total_diff > 0:
                        cpu_avg = round(((total_diff - idle_diff) / total_diff) * 100, 2)
            except Exception:
                pass

            mem_util = round(mem_util_percent, 2)
            status = "Unknown (Off or No Agent)"
            reason = ""

            # ==========================================
            # 4. RESOURCE OPTIMIZATION LOGIC: DUAL PATH
            # ==========================================
            if dcgm_available:
                # PATH A: We have Ground Truth GPU Data
                gpu_temp = dcgm_data.get("gpu_temp", 0)
                power = dcgm_data.get("power_usage", 0)
                gpu_util = dcgm_data.get("gpu_util", 0)
                
                vram_used = dcgm_data.get("vram_used_mb", 0)
                vram_free = dcgm_data.get("vram_free_mb", 0)
                vram_total = vram_used + vram_free
                vram_percent = round((vram_used / vram_total * 100) if vram_total > 0 else 0, 2)

                # Inject derived VRAM percent back into dict for the AI payload
                dcgm_data["vram_utilization_percent"] = vram_percent

                if gpu_temp > 82.0 or gpu_util > 95.0 or vram_percent > 95.0:
                    status = "Under provisioned"
                    reason = f"DCGM Active: Bottleneck risk! GPU core is {gpu_util}% active, VRAM is {vram_percent}% full, Temp is {gpu_temp}°C."
                elif gpu_util < 2.0 and vram_percent < 5.0:
                    status = "Idle"
                    reason = f"DCGM Active: Complete waste. GPU engine is at {gpu_util}% and VRAM is mostly empty ({vram_percent}%). It is doing nothing."
                elif gpu_util > 40.0 or vram_percent > 50.0:
                    status = "Optimized"
                    reason = f"DCGM Active: High ROI. Model is loaded (VRAM {vram_percent}%) and actively computing (Engine {gpu_util}%, {power}W)."
                else:
                    status = "Over provisioned"
                    reason = f"DCGM Active: Hardware is too big for the workload. Engine is only at {gpu_util}% and VRAM at {vram_percent}%."
            else:
                # PATH B: DCGM Blocked/Offline -> Fallback to System Proxy
                if cpu_avg < 3.0 and mem_util < 15.0 and load_15 < 0.5:
                    status = "Idle"
                    reason = f"DCGM inaccessible. Proxy logic: Node is practically dead. CPU {cpu_avg}%, RAM {mem_util}%."
                elif cpu_avg > 85.0 or mem_util > 90.0:
                    status = "Under provisioned"
                    reason = f"DCGM inaccessible. Proxy logic: Resource starvation risk. CPU {cpu_avg}%, RAM {mem_util}%."
                elif cpu_avg > 40.0 or mem_util > 50.0:
                    status = "Optimized"
                    reason = f"DCGM inaccessible. Proxy logic: Healthy ROI. CPU {cpu_avg}%, RAM {mem_util}%."
                else:
                    status = "Over provisioned"
                    reason = f"DCGM inaccessible. Proxy logic: Wasted overhead. CPU {cpu_avg}%, RAM {mem_util}%."

            # Update Tallies
            if status in category_counts:
                category_counts[status] += 1
            else:
                category_counts["Unknown (Off or No Agent)"] += 1

            # Build Final Payload
            gpu_inventory.append({
                "id": d_id,
                "name": name,
                "ip_address": ip_address,
                "size_slug": size_slug,
                "hourly_cost": hourly_price,
                "dcgm_accessible": dcgm_available,
                "efficiency_status": status,
                "efficiency_reason": reason,
                "metrics": {
                    "gpu_hardware": dcgm_data if dcgm_available else "Port 9400 unreachable",
                    "host_system": {
                        "cpu_avg_percent": cpu_avg,
                        "memory_utilization_percent": mem_util,
                        "load_15_min": load_15
                    }
                }
            })

    return {
        "statusCode": 200,
        "body": {
            "summary": f"Analyzed {len(gpu_inventory)} high-compute nodes out of {len(droplets)} total Droplets.",
            "insights": category_counts,
            "gpu_inventory": gpu_inventory
        }
    }

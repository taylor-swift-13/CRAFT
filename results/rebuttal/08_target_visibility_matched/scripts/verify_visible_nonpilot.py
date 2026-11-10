"""Run the unchanged verifier on nonpilot jobs while pilot verification finishes."""
import craft16_target_visible as driver


if __name__ == '__main__':
    original_prepare = driver.prepare
    def prepare_nonpilot():
        jobs, manifest, pilot = original_prepare()
        return [job for job in jobs if job['key'] not in pilot], manifest, pilot
    driver.prepare = prepare_nonpilot
    driver.main()

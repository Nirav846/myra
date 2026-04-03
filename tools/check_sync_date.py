from myra_app.librarian import Librarian
lib = Librarian(read_only=True)
print(f"Max Price Date: {lib.get_max_price_date()}")
print(f"Max Insider Date: {lib.get_max_insider_date()}")
lib._tech_conn.close()
lib._inst_conn.close()
lib._meta_conn.close()
lib._val_conn.close()

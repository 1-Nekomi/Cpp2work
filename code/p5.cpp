//求微调总费用
#include<iostream>
using namespace std;
const double PRICE=2.35;
int main()
{
	cout<<"2026111308陈依琳"<<endl;
	double hours;      //存放小时数
    double total;       //存放费用
	cout<<"请输入微调运行的小时数：";
	cin>>hours;
	total=hours*PRICE;
	cout<<"微调总费用为：";     //计算费用
	cout<<fixed;cout.precision(2);cout<<total<<"元"<<endl;
	return 0;
}